"""Real n8n graph compiler/CLI adapter. No local DAG execution fallback."""
import json
import os
from pathlib import Path
import re
import shutil
import socket
import signal
import subprocess
import threading
import time
from datetime import datetime,timedelta
from .store import now,stamp,uid


def command():
    raw=os.environ.get('SLEEP_IN_N8N_COMMAND')
    if raw:
        value=json.loads(raw)
        if not isinstance(value,list) or not value or any(not isinstance(v,str) for v in value):raise ValueError('SLEEP_IN_N8N_COMMAND must be a JSON argv array')
        if not shutil.which(value[0]):raise ValueError('Configured n8n executable is unavailable')
        return value
    executable=shutil.which('n8n')
    if not executable:raise ValueError('Install native n8n and configure SLEEP_IN_N8N_COMMAND')
    return [executable]


def compile_graph(run,base_url):
    rid=run['id'];nodes=[];connections={}
    def add_connection(source,target,index=0):
        connections.setdefault(source,{'main':[[]]})['main'][0].append({'node':target,'type':'main','index':index})
    def http_node(name,endpoint,position):
        nodes.append({'id':name,'name':name,'type':'n8n-nodes-base.httpRequest','typeVersion':4.2,'position':position,'executeOnce':True,'parameters':{'method':'POST','url':base_url.rstrip('/')+f'/internal/workflows/{rid}/'+endpoint,'sendHeaders':True,'headerParameters':{'parameters':[{'name':'x-workflow-token','value':run['callback_token']}]},'options':{'timeout':min((run['timeout']+30)*1000,2147483647)}}})
    def barrier(parents,target):
        previous=parents[0]
        for index,parent in enumerate(parents[1:],1):
            name=f'barrier_{target}_{index}'
            nodes.append({'id':name,'name':name,'type':'n8n-nodes-base.merge','typeVersion':3,'position':[700,index*100],'parameters':{'mode':'append','numberInputs':2}})
            add_connection(previous,name,0);add_connection(parent,name,1);previous=name
        add_connection(previous,target)
    nodes.append({'id':'start','name':'start','type':'n8n-nodes-base.manualTrigger','typeVersion':1,'position':[0,200],'parameters':{}})
    graph=run['snapshot']
    for index,node in enumerate(graph['nodes']):
        name='node_'+node['id'];http_node(name,'nodes/'+node['id'],[300,index*140])
        parents=['node_'+e['source'] for e in graph['edges'] if e['target']==node['id']]
        if not parents:add_connection('start',name)
        elif len(parents)==1:add_connection(parents[0],name)
        else:barrier(parents,name)
    http_node('finish','finish',[1000,200])
    leaves=['node_'+n['id'] for n in graph['nodes'] if not any(e['source']==n['id'] for e in graph['edges'])]
    if len(leaves)==1:add_connection(leaves[0],'finish')
    elif leaves:barrier(leaves,'finish')
    else:add_connection('start','finish')
    return {'id':rid,'name':'Sleep In '+rid,'active':False,'nodes':nodes,'connections':connections,'settings':{'executionOrder':'v1'},'pinData':{},'versionId':rid}


def execute_graph(service,rid):
    store=service.store
    with store.transaction() as tx:
        run=tx.get('workflow_run',rid)
        if not run or run['status'] not in {'queued','running'}:return
        run['status']='running';run['started_at']=run['started_at'] or stamp();tx.put('workflow_run',run)
    directory=store.path/'workflow-runs'/rid;directory.mkdir(parents=True,exist_ok=True,mode=0o700)
    logfile=directory/'n8n.log';proc=None;started=time.monotonic()
    try:
        argv=command();base=os.environ.get('SLEEP_IN_BASE_URL','http://127.0.0.1:8080')
        graph=compile_graph(run,base);file=directory/'n8n-graph.json';file.write_text(json.dumps(graph));file.chmod(0o600)
        env={key:value for key,value in os.environ.items() if not key.startswith(('DB_','N8N_','QUEUE_','EXECUTIONS_'))}
        env.update(N8N_USER_FOLDER=str(directory/'n8n'),N8N_DIAGNOSTICS_ENABLED='false',N8N_VERSION_NOTIFICATIONS_ENABLED='false',N8N_RUNNERS_ENABLED='false',N8N_ENCRYPTION_KEY=run['callback_token'],N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS='true',N8N_TEMPLATES_ENABLED='false',N8N_COMMUNITY_PACKAGES_ENABLED='false',N8N_LOG_LEVEL='info',DB_TYPE='sqlite',EXECUTIONS_DATA_SAVE_ON_SUCCESS='all',EXECUTIONS_DATA_SAVE_ON_ERROR='all')
        with socket.socket() as port_socket:
            port_socket.bind(('127.0.0.1',0));broker_port=port_socket.getsockname()[1]
        env['N8N_RUNNERS_BROKER_PORT']=str(broker_port)
        env['N8N_RUNNERS_BROKER_LISTEN_ADDRESS']='127.0.0.1'
        env['PATH']=str(Path(shutil.which(argv[0])).parent)+os.pathsep+env.get('PATH','/usr/bin:/bin')
        for action in ([*argv,'import:workflow','--input='+str(file)],[*argv,'execute','--id='+rid,'--rawOutput']):
            if service._cancelled(rid):break
            with logfile.open('a') as output:
                proc=subprocess.Popen(action,stdout=output,stderr=subprocess.STDOUT,env=env,cwd=directory,start_new_session=True)
                with store.transaction() as tx:
                    current=tx.get('workflow_run',rid);current['adapter_pid']=proc.pid;tx.put('workflow_run',current)
                while proc.poll() is None:
                    current=service.get_run(rid)
                    timeout=time.monotonic()-started>run['timeout']+30
                    if current['status'] in {'cancelling','cancelled','timed_out'} or timeout:
                        if timeout:
                            with store.transaction() as tx:
                                current=tx.get('workflow_run',rid);current.update(status='timed_out',error='Workflow deadline exceeded',finished_at=stamp());tx.put('workflow_run',current)
                        os.killpg(proc.pid,signal.SIGTERM)
                        try:proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                        break
                    time.sleep(.15)
                if proc.returncode and service.get_run(rid)['status'] not in {'cancelling','cancelled','timed_out'}:raise ValueError(f'n8n command exited {proc.returncode}')
        current=service.get_run(rid)
        if current['status']=='cancelling':
            # Active workers observe the cancelling state before completion is confirmed.
            deadline=time.monotonic()+5
            while any(n['status']=='running' for n in service.get_run(rid)['nodes'].values()) and time.monotonic()<deadline:time.sleep(.1)
            service.finish(rid)
        elif current['status'] in {'queued','running'}:raise ValueError('n8n ended without an authenticated terminal callback')
    except Exception as exc:
        with store.transaction() as tx:
            current=tx.get('workflow_run',rid)
            if current and current['status'] in {'queued','running'}:
                current.update(status='failed',error=str(exc),finished_at=stamp())
                for node in current['nodes'].values():
                    if node['status']=='queued':node.update(status='not_run',reason='orchestration_failed',finished_at=stamp())
                tx.put('workflow_run',current)
    finally:
        log=logfile.read_text(errors='replace')[-50000:] if logfile.exists() else ''
        log=log.replace(run['callback_token'],'[redacted]')
        match=re.search(r'(?:Execution ID|Execution finished with ID|executionId)[\s:"=]+([0-9]+)',log,re.I)
        with store.transaction() as tx:
            current=tx.get('workflow_run',rid)
            if current:
                current['adapter_log']=log;current['adapter_pid']=None
                if match:current['n8n_execution_id']=match.group(1)
                # Local n8n execution storage is independent evidence when CLI output omits its ID.
                if not current.get('n8n_execution_id'):
                    import sqlite3
                    dbpath=directory/'n8n'/'.n8n'/'database.sqlite'
                    if dbpath.exists():
                        try:
                            with sqlite3.connect(f'file:{dbpath}?mode=ro',uri=True) as db:
                                record=db.execute('SELECT id FROM execution_entity WHERE workflowId=? ORDER BY id DESC LIMIT 1',(rid,)).fetchone()
                                if record:current['n8n_execution_id']=str(record[0])
                        except sqlite3.Error:pass
                current['adapter_finished_at']=stamp();tx.put('workflow_run',current)


def dispatch_pending(service):
    launched=[]
    with service.store.transaction() as tx:
        active=sum(1 for r in tx.all('workflow_run') if r.get('adapter_lease') and not r.get('adapter_finished_at'))
        for run in sorted(tx.all('workflow_run'),key=lambda r:r['created_at']):
            if active>=2:break
            if run['status']!='queued' or run.get('adapter_lease'):continue
            run.update(adapter_lease=uid(),adapter_claimed_at=stamp());tx.put('workflow_run',run);launched.append(run['id']);active+=1
    for rid in launched:
        thread=threading.Thread(target=execute_graph,args=(service,rid),daemon=True,name='workflow-'+rid)
        service._threads[rid]=thread;thread.start()
    return launched


def worker_lock(directory):
    import fcntl
    handle=(Path(directory)/'workflow-worker.lock').open('a+')
    try:fcntl.flock(handle.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:
        handle.close();raise RuntimeError('Workflow worker is already running')
    handle.seek(0);handle.truncate();handle.write(str(os.getpid()));handle.flush()
    return handle


def recover_interrupted(service):
    # Runs left by an earlier worker cannot be replayed safely after an uncertain write.
    with service.store.transaction() as tx:
        for run in tx.all('workflow_run'):
            if run['status'] in {'running','cancelling'} or run['status']=='queued' and run.get('adapter_lease') and not run.get('adapter_finished_at'):
                run.update(status='failed',error='Worker interrupted; external effects may be uncertain. Explicit rerun required.',finished_at=stamp(),adapter_finished_at=stamp())
                for node in run['nodes'].values():
                    if node['status'] in {'running','queued'}:node.update(status='not_run',reason='interrupted_unknown_effect',finished_at=stamp())
                tx.put('workflow_run',run)
            elif run.get('adapter_lease') and not run.get('adapter_finished_at'):
                run['adapter_finished_at']=stamp();tx.put('workflow_run',run)


def worker_loop(service):
    ownership=worker_lock(service.store.path)
    instance_id=os.environ.get('SLEEP_IN_INSTANCE_ID')
    stopping=threading.Event()
    def stop(signum,frame):stopping.set()
    signal.signal(signal.SIGTERM,stop);signal.signal(signal.SIGINT,stop)
    recover_interrupted(service)
    while not stopping.is_set():
        try:command();ready=True;reason=None
        except Exception as exc:ready=False;reason=str(exc)
        with service.store.transaction() as tx:tx.put('meta',{'id':'workflow_worker','status':'ready' if ready else 'unavailable','instance_id':instance_id,'pid':os.getpid(),'last_seen':stamp(),'engine':'n8n','n8n_available':ready,'reason':reason})
        try:
            service.tick()
            if ready:service.dispatch_pending()
        except Exception as exc:
            with service.store.transaction() as tx:tx.put('workflow_event',{'id':uid(),'reason':'worker_tick_failed','message':str(exc),'created_at':stamp()})
        stopping.wait(2)
    # Explicit worker termination cancels owned graph/process groups, never leaves a new scheduler.
    for rid,thread in service._threads.items():
        if thread.is_alive():service.cancel(rid)
    for thread in service._threads.values():thread.join(timeout=10)
    with service.store.transaction() as tx:tx.put('meta',{'id':'workflow_worker','status':'stopped','instance_id':instance_id,'pid':os.getpid(),'last_seen':stamp(),'engine':'n8n'})
    ownership.close()
