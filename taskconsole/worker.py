"""Durable queue consumer with bounded concurrency and non-replaying recovery."""
import os
import shutil
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta
from .runtime import execute
from .service import TERMINAL
from .store import now,stamp,uid


class Worker:
    def __init__(self,store):
        self.store=store
        self.worker_id=uid()
        self.stopping=threading.Event()

    def recover(self):
        count=0
        with self.store.transaction() as tx:
            for run in tx.all('execution'):
                if run['status'] in {'running','cancelling'} and (not run.get('lease') or datetime.fromisoformat(run['lease'])<now()):
                    run.update(status='interrupted',finished_at=stamp(),reason='worker_lost',lease=None)
                    tx.put('execution',run)
                    count+=1
            # Publishing in a previous server process may have been interrupted.
            for version in tx.all('version'):
                if version['status']=='building' and (now()-datetime.fromisoformat(version['created_at'])).total_seconds()>1800:
                    version.update(status='failed',build_log='Publication interrupted. Publish the draft again.');tx.put('version',version)

        return count

    def claim(self):
        with self.store.transaction() as tx:
            settings=tx.get('meta','settings');settings['worker']={'id':self.worker_id,'last_seen':stamp()};tx.put('meta',settings)
            runs=tx.all('execution')
            if sum(r['status'] in {'running','cancelling'} for r in runs)>=settings['concurrency']:return None
            queued=sorted((r for r in runs if r['status']=='queued'),key=lambda r:r['created_at'])
            if not queued:return None
            run=queued[0]
            run.update(status='running',started_at=stamp(),worker_id=self.worker_id,lease=(now()+timedelta(seconds=45)).isoformat())
            tx.put('execution',run);return run

    def run_once(self):
        run=self.claim()
        if not run:return False
        self.perform(run)
        return True

    def perform(self,run):
        rid=run['id'];folder=self.store.path/'runs'/rid;folder.mkdir(mode=0o700,parents=True,exist_ok=True)
        done=threading.Event()
        secret_values=[]
        def heartbeat():
            while not done.wait(5):
                with self.store.transaction() as tx:
                    current=tx.get('execution',rid)
                    if not current or current['status'] in TERMINAL:return
                    current['lease']=(now()+timedelta(seconds=45)).isoformat();tx.put('execution',current)
        thread=threading.Thread(target=heartbeat,daemon=True);thread.start()
        try:
            with self.store.transaction() as tx:
                version=tx.get('version',run['version_id']);variables=tx.all('variable')
            env={'PATH':os.defpath,'LANG':'C.UTF-8','TASK_RUN_ID':rid}
            configured={}
            for scope in ('instance',run['script_id']):
                for v in variables:
                    if v['scope']==scope:configured[v['name']]=self.store.fernet.decrypt(v['encrypted'].encode()).decode()
            for name in version.get('manifest',{}).get('required_variables',[]):
                if name not in configured:raise ValueError('Missing required variable: '+name)
            env.update(configured)
            secret_values=sorted({value for value in configured.values() if value},key=len,reverse=True)
            pending={'stdout':'','stderr':''};guard=threading.Lock();keep=max([len(v) for v in secret_values]+[1])
            def emit(stream,text,flush=False):
                with guard:
                    pending[stream]+=text
                    buffer=pending[stream]
                    # Hold a full secret-sized tail to handle secrets split across reads.
                    limit=len(buffer) if flush else max(0,len(buffer)-keep)
                    for value in secret_values:
                        pos=buffer.find(value)
                        while pos>=0:
                            if pos<limit<pos+len(value):limit=pos
                            pos=buffer.find(value,pos+1)
                    ready=buffer[:limit];pending[stream]=buffer[limit:]
                    for value in secret_values:ready=ready.replace(value,'[REDACTED]')
                    if ready:
                        with (folder/(stream+'.txt')).open('a',encoding='utf-8') as out:out.write(ready)
            def cancel():
                with self.store.transaction() as tx:
                    current=tx.get('execution',rid)
                    return self.stopping.is_set() or not current or current['status'] in {'cancelling','interrupted'}
            # Run from a copy so a normal script writing beside __file__ cannot mutate
            # the published version or another run's working directory.
            workspace=folder/'workspace'
            shutil.copytree(self.store.path/'scripts'/run['version_id'],workspace)
            result=execute(workspace,version['mode'],run['params'],folder/'outputs',version['runtime']['python'],run['timeout'],cancel,emit,env)
            emit('stdout','',True);emit('stderr','',True)
            if self.stopping.is_set() and result['status']=='cancelled':result.update(status='interrupted',reason='worker_stopped')
        except Exception as exc:
            reason=str(exc)
            for value in secret_values:reason=reason.replace(value,'[REDACTED]')
            result={'status':'failed','reason':reason[:2000],'exit_code':None,'artifacts':[]}
        finally:
            done.set();thread.join(timeout=6)
        with self.store.transaction() as tx:
            current=tx.get('execution',rid)
            if current and current['status'] not in TERMINAL:
                current.update(result,finished_at=stamp(),lease=None)
                tx.put('execution',current)
                task=tx.get('task',current['task_id'])
                if task and task.get('last_run',{}).get('id')==rid:
                    task['last_run']['status']=current['status'];tx.put('task',task)
        shutil.rmtree(folder/'workspace',ignore_errors=True)

    def cleanup(self):
        with self.store.transaction() as tx:
            settings=tx.get('meta','settings');current=now()
            for run in tx.all('execution'):
                if run['status'] not in TERMINAL:continue
                age=(current-datetime.fromisoformat(run['finished_at'] or run['created_at'])).total_seconds()/86400
                if age>settings['log_retention_days'] and not run['logs_expired']:
                    shutil.rmtree(self.store.path/'runs'/run['id'],ignore_errors=True)
                    run['logs_expired']=True;tx.put('execution',run)
                if age>settings['metadata_retention_days']:tx.remove('execution',run['id'])
            for session in tx.all('session'):
                if datetime.fromisoformat(session['expires_at'])<current:tx.remove('session',session['id'])
            for attempt in tx.all('attempt'):
                if (current-datetime.fromisoformat(attempt['at'])).total_seconds()>600:tx.remove('attempt',attempt['id'])

    def serve(self):
        def stop(*_):self.stopping.set()
        signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
        last_cleanup=0
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures=set()
            while not self.stopping.is_set():
                self.recover()
                futures={f for f in futures if not f.done()}
                if len(futures)<16:
                    run=self.claim()
                    if run:futures.add(pool.submit(self.perform,run))
                if time.monotonic()-last_cleanup>3600:self.cleanup();last_cleanup=time.monotonic()
                self.stopping.wait(.5)
