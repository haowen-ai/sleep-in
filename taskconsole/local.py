"""Loopback Mac service supervisor. OS integration is owned by the native app.

No login registration, global power setting or power-source policy is implicit here.
The default idle-sleep assertion deliberately applies on AC AND battery.
"""
import argparse
import fcntl
import json
import os
from pathlib import Path
import signal
import secrets
import re
import hmac
from contextlib import contextmanager
import socket
import subprocess
import sys
import threading
import time
from urllib.request import urlopen


def read_json(path, default=None):
    try: return json.loads(Path(path).read_text())
    except (FileNotFoundError, ValueError): return default


def write_json(path, value):
    path=Path(path); path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary=path.with_name(path.name+f'.{os.getpid()}.new')
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as handle:
        json.dump(value,handle); handle.flush(); os.fsync(handle.fileno())
    os.replace(temporary,path)


class InstanceLock:
    def __init__(self, directory): self.path=Path(directory)/'local-service.lock'; self.handle=None; self.generation=secrets.token_hex(16)
    def __enter__(self):
        self.path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.handle=self.path.open('a+')
        try: fcntl.flock(self.handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.handle.close(); self.handle=None
            raise RuntimeError('Sleep In is already running') from None
        self.handle.seek(0); self.handle.truncate()
        json.dump({'pid':os.getpid(),'generation':self.generation},self.handle); self.handle.flush()
        return self
    def __exit__(self,*args):
        if self.handle: self.handle.close(); self.handle=None


def is_running(directory):
    try:
        with InstanceLock(directory): return False
    except RuntimeError: return True


def should_start(directory, explicit=False):
    return explicit or read_json(Path(directory)/'local-preferences.json',{}).get('running',True)


def set_running_preference(directory, running):
    path=Path(directory)/'local-preferences.json'; prefs=read_json(path,{})
    prefs['running']=bool(running); write_json(path,prefs)


def status(directory):
    result=read_json(Path(directory)/'local-status.json',{})
    if not is_running(directory):
        result.update(state='stopped',assertion=False,components={})
    elif read_json(Path(directory)/'local-service.lock',{}).get('generation')!=result.get('generation'):
        result.update(state='starting',assertion=False,components={})
    elif result.get('state') in {'running','draining','degraded'}:
        seen=result.get('seen_at')
        age=time.time()-seen if type(seen) in (int,float) else float('inf')
        if not 0<=age<15:
            result.update(state='degraded',assertion=False,assertion_state='unverified',components={},
                          reason='Supervisor health snapshot is stale; current component and power states are unverified')
    if result.get('state')=='running' and (Path(directory)/'local-stop-request.json').exists():
        result['state']='draining'
    result['running_preference']=should_start(directory)
    result['power']=power_status()
    result['next_scheduled']=next_scheduled(directory)
    return result


def parse_power(text):
    source='battery' if "'Battery Power'" in text else 'ac' if "'AC Power'" in text else 'unknown'
    percent=re.search(r'(\d{1,3})%',text);percent=int(percent.group(1)) if percent else None
    return {'source':source,'percent':percent,'low':source=='battery' and percent is not None and percent<=15}


def power_status():
    if sys.platform!='darwin':return parse_power('')
    try:return parse_power(subprocess.run(['/usr/bin/pmset','-g','batt'],capture_output=True,text=True,timeout=3).stdout)
    except (OSError,subprocess.TimeoutExpired):return parse_power('')


def next_scheduled(directory):
    # Read-only SQL avoids Store initialization / business writes in health checks.
    import sqlite3
    database=Path(directory)/'console.db'
    if not database.exists():return None
    try:
        with sqlite3.connect('file:'+str(database)+'?mode=ro',uri=True,timeout=1) as connection:
            rows=connection.execute("SELECT payload FROM console_records WHERE kind='workflow'").fetchall()
        candidates=[]
        from .schedule import workflow_next_runs
        from datetime import datetime,timezone
        current=datetime.now(timezone.utc)
        for row in rows:
            workflow=json.loads(row[0]);triggers=list(workflow.get('triggers',[]))
            if workflow.get('enabled'):triggers.append({'enabled':True,'kind':'scheduled','schedule':workflow.get('schedule',{}),'timezone':workflow.get('timezone','UTC')})
            for trigger in triggers:
                if not trigger.get('enabled') or trigger.get('kind')!='scheduled':continue
                dates=workflow_next_runs(trigger.get('schedule',{}),trigger.get('timezone',workflow.get('timezone','UTC')),current,1)
                if dates:candidates.append({'workflow':workflow.get('name',''),'at':dates[0].isoformat(),'timezone':trigger.get('timezone',workflow.get('timezone','UTC'))})
        return min(candidates,key=lambda value:value['at']) if candidates else None
    except (OSError,ValueError,sqlite3.Error):return None


@contextmanager
def update_start_guard(directory):
    install=Path(directory).resolve().parent
    lock_path=install.parent/('.'+install.name+'-update.lock')
    token_path=install.parent/('.'+install.name+'-update-token')
    supplied=os.environ.get('SLEEP_IN_UPDATE_TOKEN','')
    expected=read_json(token_path,{}).get('token','')
    if supplied and expected and hmac.compare_digest(supplied,expected):
        yield;return
    # Hold a shared lock throughout startup, closing the check-to-launch race.
    lock_path.parent.mkdir(parents=True,exist_ok=True)
    with lock_path.open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_SH|fcntl.LOCK_NB)
        except BlockingIOError:raise RuntimeError('A verified update is in progress; wait before starting Sleep In') from None
        yield


def assert_update_allowed(directory):
    with update_start_guard(directory):pass


@contextmanager
def guarded_instance_lock(directory):
    instance=InstanceLock(directory)
    with update_start_guard(directory):instance.__enter__()
    try:yield instance
    finally:instance.__exit__()


def child_environment(directory, config, inherited=None):
    env=dict(os.environ if inherited is None else inherited)
    directory=Path(directory).resolve()
    env.update(APP_HOST='127.0.0.1',APP_PORT=str(config.get('app_port',8765)),
               APP_STATE_DIR=str(directory),STATE_DIR=str(directory),SLEEP_IN_LOCAL='1',
               DATABASE_URL=f'sqlite:///{directory}/console.db',
               SLEEP_IN_BASE_URL=f"http://127.0.0.1:{config.get('app_port',8765)}",
               SLEEP_IN_N8N_COMMAND=json.dumps(config['n8n_command']),
               PYTHONUNBUFFERED='1')
    node=Path(config['n8n_command'][0])
    if node.is_absolute(): env['PATH']=str(node.parent)+os.pathsep+env.get('PATH','/usr/bin:/bin')
    return env


def register_login(directory, consent=False):
    if not consent: raise ValueError('Explicit user consent is required for login startup')
    raise ValueError('Use Start at Login in the native Sleep In app; registration is managed by SMAppService')


def process_child(argv):
    """Pipe EOF on supervisor death terminates the owned child process group."""
    child=subprocess.Popen(argv,start_new_session=True)
    ended=threading.Event()
    def watch_parent():
        try: sys.stdin.buffer.read()
        finally: ended.set()
    threading.Thread(target=watch_parent,daemon=True).start()
    def stop(*_): ended.set()
    signal.signal(signal.SIGTERM,stop); signal.signal(signal.SIGINT,stop)
    while child.poll() is None and not ended.wait(.2): pass
    if child.poll() is None:
        os.killpg(child.pid,signal.SIGTERM)
        try: child.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(child.pid,signal.SIGKILL); child.wait()
    return child.returncode


def spawn_component(command, env, log):
    return subprocess.Popen([sys.executable,'-m','taskconsole.local','child','--',*command],
                            stdin=subprocess.PIPE,stdout=log,stderr=log,env=env)


def terminate_component(process):
    if process.stdin: process.stdin.close()
    try: process.wait(timeout=15)
    except subprocess.TimeoutExpired: process.kill(); process.wait()


def app_healthy(url, generation=None):
    try:
        with urlopen(url+'/healthz',timeout=2) as response:
            value=json.load(response)
            return response.status==200 and value.get('status')=='ok' and (generation is None or value.get('instance_id')==generation)
    except Exception: return False


def worker_health(directory, generation=None):
    from datetime import datetime, timezone
    from .store import Store
    if not (Path(directory)/'console.db').exists(): return 'unavailable'
    store=Store(directory,f'sqlite:///{directory}/console.db')
    try:
        with store.transaction() as tx: value=tx.get('meta','workflow_worker')
        if not value or value.get('status')!='ready' or not value.get('n8n_available'): return 'unavailable'
        if generation is not None and value.get('instance_id')!=generation: return 'unavailable'
        age=(datetime.now(timezone.utc)-datetime.fromisoformat(value['last_seen'])).total_seconds()
        return 'ready' if 0<=age<30 else 'unavailable'
    except (KeyError,ValueError): return 'unavailable'
    finally: store.engine.dispose()


def active_runs(directory):
    from .store import Store
    store=Store(directory,f'sqlite:///{directory}/console.db')
    try:
        with store.transaction() as tx:
            return [r for r in tx.all('workflow_run') if r.get('status') in {'queued','running','cancelling'}]
    finally: store.engine.dispose()


def cancel_active(directory):
    from .store import Store
    from .workflows import WorkflowService
    store=Store(directory,f'sqlite:///{directory}/console.db')
    try:
        service=WorkflowService(store)
        for run in active_runs(directory): service.cancel(run['id'])
    finally: store.engine.dispose()


def serve(directory):
    directory=Path(directory).resolve()
    if not should_start(directory): return 0
    with guarded_instance_lock(directory) as service_lock:
        config=read_json(directory/'local-config.json')
        if not config or not config.get('n8n_command'): raise ValueError('Local runtime configuration is missing; reopen Sleep In to repair installation')
        env=child_environment(directory,config)
        env['SLEEP_IN_INSTANCE_ID']=service_lock.generation
        # Fail before launching any component if another application owns the port.
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
            probe.bind(('127.0.0.1',int(env['APP_PORT'])))
            probe.listen(1)
        version=subprocess.run([*config['n8n_command'],'--version'],env=env,capture_output=True,text=True,timeout=60)
        if version.returncode: raise RuntimeError('n8n runtime self-check failed: '+version.stderr[-1200:])
        url=env['SLEEP_IN_BASE_URL']; started=time.time(); stopping=threading.Event()
        signal.signal(signal.SIGTERM,lambda *_: stopping.set())
        signal.signal(signal.SIGINT,lambda *_: stopping.set())
        previous=read_json(directory/'local-status.json',{})
        gap={'previous_state':previous.get('state'),'previous_seen':previous.get('seen_at'),'restored_at':started}
        children={}; assertion=None
        with (directory/'local-service.log').open('a') as log:
            try:
                if not config.get('disable_power_assertion',False):
                    if sys.platform!='darwin': raise RuntimeError('Mac idle sleep protection requires macOS')
                    assertion=subprocess.Popen(['/usr/bin/caffeinate','-i','-w',str(os.getpid())],stdout=log,stderr=log)
                children['app']=spawn_component([sys.executable,'-m','taskconsole','serve'],env,log)
                # App initializes/migrates the store before a worker can open it.
                deadline=time.monotonic()+60
                while not app_healthy(url,service_lock.generation):
                    if children['app'].poll() is not None: raise RuntimeError('Application exited during startup; see local-service.log')
                    if time.monotonic()>deadline: raise RuntimeError('Application readiness timed out')
                    if stopping.wait(.25): return 0
                children['worker']=spawn_component([sys.executable,'-m','taskconsole.workflows','worker'],env,log)
                deadline=time.monotonic()+60
                while worker_health(directory,service_lock.generation)!='ready':
                    if children['worker'].poll() is not None: raise RuntimeError('Worker exited during startup')
                    if time.monotonic()>deadline: raise RuntimeError('Worker readiness timed out')
                    if stopping.wait(.25): return 0
                while True:
                    request=read_json(directory/'local-stop-request.json')
                    if stopping.is_set() and not request:
                        request={'mode':'cancel','requested_at':time.time()}
                        write_json(directory/'local-stop-request.json',request)
                    if request:
                        if request['mode']=='cancel': cancel_active(directory)
                        if not active_runs(directory): break
                    healthy=app_healthy(url,service_lock.generation)
                    component_states={name:('running' if proc.poll() is None else 'failed') for name,proc in children.items()}
                    component_states['app']='ready' if healthy else 'unavailable'
                    component_states['worker']=worker_health(directory,service_lock.generation)
                    component_states['n8n']='available-cli'
                    component_states['power']='disabled-for-test' if config.get('disable_power_assertion') else ('held' if assertion and assertion.poll() is None else 'failed')
                    ready=healthy and component_states['worker']=='ready' and all(proc.poll() is None for proc in children.values()) and (config.get('disable_power_assertion') or assertion.poll() is None)
                    write_json(directory/'local-status.json',{'pid':os.getpid(),'generation':service_lock.generation,'state':'draining' if request else ('running' if ready else 'degraded'),
                        'assertion':bool(assertion and assertion.poll() is None),'url':url,'components':component_states,
                        'children':{name:proc.pid for name,proc in children.items()},'started_at':started,'seen_at':time.time(),'recovery':gap})
                    if not ready: raise RuntimeError('A background component failed; see local-service.log')
                    time.sleep(1)
            finally:
                for process in reversed(list(children.values())): terminate_component(process)
                if assertion:
                    assertion.terminate(); assertion.wait(timeout=5)
                write_json(directory/'local-status.json',{'state':'stopped','assertion':False,'seen_at':time.time(),'url':url,'recovery':gap})
    return 0


def start(directory, explicit=False, open_browser=False):
    with update_start_guard(directory):return _start(directory,explicit,open_browser)


def _start(directory, explicit=False, open_browser=False):
    directory=Path(directory).resolve(); directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    if not should_start(directory,explicit): return status(directory)
    launched=None
    if not is_running(directory):
        if explicit or not (directory/'local-preferences.json').exists(): set_running_preference(directory,True)
        (directory/'local-stop-request.json').unlink(missing_ok=True)
        with (directory/'local-launch.log').open('a') as log:
            launched=subprocess.Popen([sys.executable,'-m','taskconsole.local','--state',str(directory),'serve'],
                             stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
    deadline=time.monotonic()+75
    while time.monotonic()<deadline:
        result=status(directory)
        if result.get('state')=='running' and app_healthy(result.get('url',''),result.get('generation')):
            if open_browser:
                import webbrowser
                webbrowser.open(result['url'])
            return result
        if launched is not None and launched.poll() is not None and not is_running(directory):
            raise RuntimeError('Background startup failed; see local-launch.log and local-service.log')
        time.sleep(.5)
    raise RuntimeError('Background startup did not become ready; see local-launch.log and local-service.log')


def request_stop(directory, mode='finish'):
    if mode not in {'finish','cancel'}: raise ValueError('Choose finish or cancel')
    set_running_preference(directory,False)
    write_json(Path(directory)/'local-stop-request.json',{'mode':mode,'requested_at':time.time()})
    return status(directory)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state',default=os.environ.get('APP_STATE_DIR',str(Path.home()/'Library/Application Support/Sleep In/state')))
    sub=parser.add_subparsers(dest='command',required=True)
    start_parser=sub.add_parser('start'); start_parser.add_argument('--explicit',action='store_true'); start_parser.add_argument('--open',action='store_true')
    sub.add_parser('serve'); sub.add_parser('status');sub.add_parser('check-update')
    stop_parser=sub.add_parser('stop'); stop_parser.add_argument('--mode',choices=['finish','cancel'],required=True)
    child=sub.add_parser('child'); child.add_argument('argv',nargs=argparse.REMAINDER)
    args=parser.parse_args()
    try:
        if args.command=='child': return process_child(args.argv[1:] if args.argv[:1]==['--'] else args.argv)
        if args.command=='serve': return serve(args.state)
        if args.command=='check-update':assert_update_allowed(args.state);return 0
        if args.command=='start': result=start(args.state,args.explicit,args.open)
        elif args.command=='stop': result=request_stop(args.state,args.mode)
        else: result=status(args.state)
        print(json.dumps(result)); return 0
    except (ValueError,RuntimeError,OSError,subprocess.TimeoutExpired) as exc:
        print(str(exc),file=sys.stderr); return 1


if __name__=='__main__': sys.exit(main())
