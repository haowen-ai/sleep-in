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
    def link(source,target,index=0,output=0):
        outputs=connections.setdefault(source,{'main':[]})['main']
        while len(outputs)<=output:outputs.append([])
        outputs[output].append({'node':target,'type':'main','index':index})
    def http(name,endpoint,position,method='POST',body=None):
        parameters={'method':method,'url':base_url.rstrip('/')+f'/internal/workflows/{rid}/'+endpoint,'sendHeaders':True,'headerParameters':{'parameters':[{'name':'x-workflow-token','value':run['callback_token']}]},'options':{'timeout':10000}}
        if body is not None:parameters.update(sendBody=True,specifyBody='json',jsonBody=body)
        nodes.append({'id':name,'name':name,'type':'n8n-nodes-base.httpRequest','typeVersion':4.2,'position':position,'executeOnce':True,'parameters':parameters})
    def condition(name,field,position):
        nodes.append({'id':name,'name':name,'type':'n8n-nodes-base.if','typeVersion':2.2,'position':position,'parameters':{'conditions':{'options':{'caseSensitive':True,'leftValue':'','typeValidation':'strict','version':2},'conditions':[{'id':name,'leftValue':'={{ $json.'+field+' }}','rightValue':True,'operator':{'type':'boolean','operation':'true','singleValue':True}}],'combinator':'and'},'options':{}}})
    def barrier(parents,target):
        previous=parents[0]
        for index,parent in enumerate(parents[1:],1):
            name=f'barrier_{target}_{index}'
            nodes.append({'id':name,'name':name,'type':'n8n-nodes-base.merge','typeVersion':3,'position':[700,index*100],'parameters':{'mode':'append','numberInputs':2}})
            link(previous,name,0);link(parent,name,1);previous=name
        link(previous,target)
    nodes.append({'id':'start','name':'start','type':'n8n-nodes-base.manualTrigger','typeVersion':1,'position':[0,200],'parameters':{}})
    http('claim','claim',[100,200],body='={{ {execution_id: String($execution.id)} }}');link('start','claim')
    graph=run['snapshot'];groups={}
    for index,node in enumerate(graph['nodes']):
        nid=node['id'];submit='submit_'+nid;poll='status_'+nid;wait='wait_'+nid;done='done_'+nid;retry='retry_'+nid;name='node_'+nid
        http(submit,'nodes/'+nid+'/submit',[300,index*160],body='{"retry":true}')
        http(poll,'nodes/'+nid+'/status',[500,index*160],method='GET')
        nodes.append({'id':wait,'name':wait,'type':'n8n-nodes-base.wait','typeVersion':1.1,'position':[450,index*160+60],'parameters':{'resume':'timeInterval','amount':0.2,'unit':'seconds'}})
        condition(done,'terminal',[650,index*160]);condition(retry,'retry_ready',[650,index*160+60])
        nodes.append({'id':name,'name':name,'type':'n8n-nodes-base.noOp','typeVersion':1,'position':[850,index*160],'parameters':{}})
        link(submit,wait);link(wait,poll);link(poll,done);link(done,name,output=0);link(done,retry,output=1);link(retry,submit,output=0);link(retry,wait,output=1)
        parents=tuple(sorted({'node_'+e['source'] for e in graph['edges'] if e['target']==nid}))
        groups.setdefault(parents,[]).append(nid)
    for parents,children in groups.items():
        if len(children)==1:entry='submit_'+children[0]
        else:
            # Submit siblings before polling any one of them. n8n v1 otherwise visits
            # an entire polling branch depth-first and serializes independent workers.
            entry='initial_'+children[0]
            for index,nid in enumerate(children):
                initial='initial_'+nid
                http(initial,'nodes/'+nid+'/submit',[250,index*160],body='{"retry":false}')
                if index:link('initial_'+children[index-1],initial)
            for nid in children:link('initial_'+children[-1],'wait_'+nid)
        if not parents:link('claim',entry)
        elif len(parents)==1:link(parents[0],entry)
        else:barrier(list(parents),entry)
    http('finish','finish',[1000,200])
    leaves=['node_'+n['id'] for n in graph['nodes'] if not any(e['source']==n['id'] for e in graph['edges'])]
    if len(leaves)==1:link(leaves[0],'finish')
    elif leaves:barrier(leaves,'finish')
    else:link('claim','finish')
    return {'id':rid,'name':'Sleep In '+rid,'active':False,'nodes':nodes,'connections':connections,'settings':{'executionOrder':'v1'},'pinData':{},'versionId':rid}


def execute_graph(service,rid):
    store=service.store
    with store.transaction() as tx:
        run=tx.get('workflow_run',rid)
        if not run or run['status'] not in {'queued','running'} or run.get('adapter_execution_owner'):return
        run['adapter_execution_owner']=uid()
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
                    service._cancelled(rid)
                    current=service.get_run(rid)
                    timeout=time.monotonic()-started>run['timeout']
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
                message=str(exc)
                if any(node.get('process_started') for node in current['nodes'].values()):
                    message+='; orchestration interrupted, external effects may be uncertain. Explicit rerun required.'
                current.update(status='failed',error=message,finished_at=stamp())
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
        service.on_terminal(rid)


def dispatch_pending(service):
    launched=[]
    with service.store.transaction() as tx:
        active=sum(1 for r in tx.all('workflow_run') if r.get('adapter_lease') and not r.get('adapter_finished_at'))
        occupied={r['workflow_id'] for r in tx.all('workflow_run') if r['status'] in {'running','cancelling'} or (r.get('adapter_lease') and not r.get('adapter_finished_at'))}
        for run in sorted(tx.all('workflow_run'),key=lambda r:r['created_at']):
            if active>=2:break
            if run['status']!='queued' or run.get('adapter_lease') or run['workflow_id'] in occupied:continue
            run.update(adapter_lease=uid(),adapter_claimed_at=stamp());tx.put('workflow_run',run);launched.append(run['id']);active+=1;occupied.add(run['workflow_id'])
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
    from .workflows_execution import terminate_orphan
    with service.store.transaction() as tx:candidates=tx.all('workflow_run')
    for candidate in candidates:
        # An adapter may fail before its detached node process exits. Terminal
        # run status alone is not evidence that owned processes were reclaimed.
        for state in candidate['nodes'].values():
            if state.get('worker_pid') and (candidate['status'] in {'queued','running','cancelling'} or state['status'] in {'running','dispatching'}):
                termination=terminate_orphan(state['worker_pid'],state.get('worker_identity'),service.store.path/'workflow-runs'/candidate['id'])
                with service.store.transaction() as tx:
                    current=tx.get('workflow_run',candidate['id'])
                    if current:current['recovery_worker_termination']=termination;tx.put('workflow_run',current)
    # Runs left by an earlier worker cannot be replayed safely after an uncertain write.
    with service.store.transaction() as tx:
        for run in tx.all('workflow_run'):
            stranded=any(node.get('worker_pid') and node['status'] in {'running','dispatching'} for node in run['nodes'].values())
            interrupted_active=run['status'] in {'running','cancelling'} or run['status']=='queued' and run.get('adapter_lease') and not run.get('adapter_finished_at')
            if stranded or interrupted_active:
                interrupted_active=run['status'] in {'queued','running','cancelling'}
                recovered_at=stamp();note='Worker interrupted; external effects may be uncertain. Explicit rerun required.'
                if interrupted_active:
                    run.update(status='failed',error=note,finished_at=recovered_at,adapter_finished_at=recovered_at)
                else:
                    # Cleanup must not rewrite a recorded terminal outcome or its chronology.
                    run.update(recovered_at=recovered_at,recovery_note=note)
                for node in run['nodes'].values():
                    if node['status'] in {'running','queued','dispatching'} and (interrupted_active or node.get('worker_pid')):
                        node.update(status='not_run',reason='interrupted_unknown_effect',finished_at=stamp())
                        for attempt in node.get('attempts',[]):
                            if attempt.get('status')=='running':attempt.update(status='failed',error='Worker interrupted; external effects may be uncertain',finished_at=stamp())
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
            from .workflows_operations import WorkflowOperations
            WorkflowOperations(service.store).tick()
            # Accepted runs must settle even when the engine disappears. The
            # adapter records the actionable command error without executing
            # any node; keeping them queued would imply they can still run.
            service.dispatch_pending()
        except Exception as exc:
            with service.store.transaction() as tx:tx.put('workflow_event',{'id':uid(),'reason':'worker_tick_failed','message':str(exc),'created_at':stamp()})
        stopping.wait(2)
    # Explicit worker termination cancels owned graph/process groups, never leaves a new scheduler.
    for rid,thread in service._threads.items():
        if thread.is_alive():service.cancel(rid.split(':')[0])
    for thread in service._threads.values():thread.join(timeout=10)
    with service.store.transaction() as tx:tx.put('meta',{'id':'workflow_worker','status':'stopped','instance_id':instance_id,'pid':os.getpid(),'last_seen':stamp(),'engine':'n8n'})
    ownership.close()
