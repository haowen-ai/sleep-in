import json
import os
import threading
import time
import socket
from pathlib import Path
import pytest
from fastapi import FastAPI
from taskconsole.store import Store
from taskconsole.workflows import WorkflowService,register_workflow_routes


def svc(tmp_path):return WorkflowService(Store(tmp_path,'sqlite:///'+str(tmp_path/'store.sqlite')))


def test_compile_real_connections_and_join_barriers(tmp_path):
    from taskconsole.workflows_n8n import compile_graph
    service=svc(tmp_path);wf=service.save({'name':'graph','nodes':[{'id':i,'name':i,'kind':'python','source':'def main(inputs): return {}','inputs':{},'config':{'join':'all'}} for i in ['a','b','c']],'edges':[{'source':'a','target':'c'},{'source':'b','target':'c'}]})
    service.publish(wf['id']);run=service.admit(wf['id'],{})
    graph=compile_graph(run,'http://127.0.0.1:8080')
    assert len([n for n in graph['nodes'] if n['type']=='n8n-nodes-base.httpRequest'])==4
    assert any(n['type']=='n8n-nodes-base.merge' for n in graph['nodes'])
    assert all(n.get('executeOnce') for n in graph['nodes'] if n['type']=='n8n-nodes-base.httpRequest')
    assert graph['connections']['node_a']['main'][0][0]['node'].startswith('barrier_')
    assert 'source' not in json.dumps(graph)


def test_skipped_branch_and_optional_failure_join(tmp_path):
    service=svc(tmp_path)
    def node(i,source,inputs=None,config=None):return {'id':i,'name':i,'kind':'python','source':source,'inputs':inputs or {},'config':config or {}}
    graph={'name':'branch','nodes':[node('a','def main(inputs): return {"flag":True}'),node('b','def main(inputs): return {"v":7}'),node('c','def main(inputs): return {"v":9}'),node('j','def main(inputs): return inputs',{'b':{'source':'node','node_id':'b','path':'v','optional':True,'default':0},'c':{'source':'node','node_id':'c','path':'v','optional':True,'default':0}},{'join':'all'})],'edges':[{'source':'a','target':'b','condition':{'path':'flag','operator':'eq','value':True}},{'source':'a','target':'c','condition':{'path':'flag','operator':'eq','value':False}},{'source':'b','target':'j'},{'source':'c','target':'j'}]}
    wf=service.save(graph);service.publish(wf['id']);run=service.admit(wf['id'],{})
    for nid in ['a','b','c','j']:service.execute_node(run['id'],nid)
    result=service.finish(run['id'])
    assert result['status']=='succeeded' and result['nodes']['c']['status']=='skipped'
    assert result['nodes']['j']['output']['data']=={'b':7,'c':0}
    graph['nodes'][1]['source']='def main(inputs): raise RuntimeError("failed branch")'
    graph['edges'][1].pop('condition');graph['edges'][2]['required']=False
    wf=service.save(graph);service.publish(wf['id']);run=service.admit(wf['id'],{})
    for nid in ['a','b','c','j']:service.execute_node(run['id'],nid)
    result=service.finish(run['id'])
    assert result['status']=='partial' and result['nodes']['j']['output']['data']=={'b':0,'c':9}


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='Actual native n8n command not configured')
def test_actual_n8n_sql_python_javascript_graph(tmp_path,monkeypatch):
    import uvicorn
    from taskconsole.workflows_n8n import execute_graph
    app=FastAPI();store=Store(tmp_path,'sqlite:///'+str(tmp_path/'store.sqlite'))
    service=register_workflow_routes(app,store,lambda request,tx,admin=False:{'id':'test','role':'admin'})
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    deadline=time.time()+10
    while not server.started and time.time()<deadline:time.sleep(.05)
    monkeypatch.setenv('SLEEP_IN_BASE_URL',f'http://127.0.0.1:{port}')
    try:
        wf=service.save(service.templates()[0]);service.publish(wf['id']);run=service.admit(wf['id'],{})
        execute_graph(service,run['id'])
        result=service.get_run(run['id'])
        assert result['status']=='succeeded',result.get('adapter_log','')+' '+str(result.get('error'))
        assert result['nodes']['summary']['output']['data']=={'summary':{'count':3,'total':'30.75'}}
        assert result['nodes']['report']['output']['data']=={'message':'3 orders • 30.75'}
        assert all(len(n['attempts'])==1 for n in result['nodes'].values())
        assert Path(result['artifacts'][0]['path']).read_text()=='3 orders • 30.75\n'
        assert result.get('n8n_execution_id')
    finally:server.should_exit=True;thread.join(timeout=10)


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='Actual native n8n command not configured')
def test_actual_n8n_branch_merge_no_input_and_scheduled_occurrence(tmp_path,monkeypatch):
    import uvicorn
    from datetime import timedelta
    from taskconsole.store import now
    from taskconsole.workflows_n8n import execute_graph
    app=FastAPI();store=Store(tmp_path,'sqlite:///'+str(tmp_path/'store.sqlite'))
    service=register_workflow_routes(app,store,lambda request,tx,admin=False:{'id':'test','role':'admin'})
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    deadline=time.time()+10
    while not server.started and time.time()<deadline:time.sleep(.05)
    monkeypatch.setenv('SLEEP_IN_BASE_URL',f'http://127.0.0.1:{port}')
    try:
        graph={'name':'Actual branched graph','nodes':[
            {'id':'root','kind':'python','name':'Start','source':'def main(inputs): return {"flag":True,"unused":99}','inputs':{},'config':{}},
            {'id':'selected','kind':'python','name':'No input consumer','source':'def main(inputs):\n assert inputs == {}\n return {"v":7}','inputs':{},'config':{}},
            {'id':'skipped','kind':'python','name':'Unselected','source':'def main(inputs): raise RuntimeError("must never execute")','inputs':{},'config':{}},
            {'id':'join','kind':'javascript','name':'Join','source':'function main(inputs){return {received:inputs}}','inputs':{'left':{'source':'node','node_id':'selected','path':'v'},'right':{'source':'node','node_id':'skipped','path':'v','optional':True,'default':0}},'config':{'join':'all'}}],
            'edges':[{'source':'root','target':'selected','condition':{'path':'flag','operator':'truthy'}},{'source':'root','target':'skipped','condition':{'path':'flag','operator':'eq','value':False}},{'source':'selected','target':'join'},{'source':'skipped','target':'join'}]}
        wf=service.save(graph);service.publish(wf['id']);run=service.admit(wf['id'])
        execute_graph(service,run['id']);result=service.get_run(run['id'])
        assert result['status']=='succeeded',result.get('adapter_log')
        assert result['nodes']['selected']['inputs']=={}
        assert result['nodes']['skipped']['status']=='skipped' and result['nodes']['skipped']['attempts']==[]
        assert result['nodes']['join']['output']['data']=={'received':{'left':7,'right':0}}
        assert result.get('n8n_execution_id')
        # Genuine clock-based admission, only two seconds of waiting; no prior occurrence replay.
        wf=service.save(service.templates()[0]);service.publish(wf['id'])
        wf['enabled']=True;wf['schedule']={'kind':'interval','every':1,'unit':'minutes','anchor':(now()+timedelta(seconds=2)).isoformat()}
        service.save(wf,wf['id']);service.tick();time.sleep(2.1)
        admitted=service.tick();assert len(admitted)==1
        assert service.tick()==[]
        scheduled=admitted[0];assert scheduled['trigger_id']=='default'
        execute_graph(service,scheduled['id']);result=service.get_run(scheduled['id'])
        assert result['status']=='succeeded',result.get('adapter_log')
        assert result['nodes']['summary']['output']['data']=={'summary':{'count':3,'total':'30.75'}}
        assert result.get('n8n_execution_id')
    finally:server.should_exit=True;thread.join(timeout=10)


def test_worker_single_instance_lock_rejects_second_owner(tmp_path):
    from taskconsole.workflows_n8n import worker_lock
    first=worker_lock(tmp_path)
    try:
        with pytest.raises(RuntimeError,match='already running'):worker_lock(tmp_path)
    finally:first.close()
    second=worker_lock(tmp_path);second.close()


def test_adapter_killed_during_cancel_confirms_terminal_state(tmp_path,monkeypatch):
    from taskconsole import workflows_n8n as adapter
    service=svc(tmp_path);wf=service.save({'name':'Cancel','nodes':[{'id':'a','kind':'python','source':'def main(inputs): return {}','inputs':{},'config':{}}],'edges':[]});service.publish(wf['id']);run=service.admit(wf['id'])
    with service.store.transaction() as tx:
        current=tx.get('workflow_run',run['id']);current['nodes']['a']['status']='running';tx.put('workflow_run',current)
    class Process:
        pid=99999999;returncode=None
        def poll(self):
            service.cancel(run['id']);return None
        def wait(self,timeout=None):
            self.returncode=-15
            with service.store.transaction() as tx:
                current=tx.get('workflow_run',run['id']);current['nodes']['a']['status']='cancelled';tx.put('workflow_run',current)
            return -15
    monkeypatch.setattr(adapter,'command',lambda:['/usr/bin/true'])
    monkeypatch.setattr(adapter.subprocess,'Popen',lambda *a,**k:Process())
    monkeypatch.setattr(adapter.os,'killpg',lambda *a:None)
    adapter.execute_graph(service,run['id'])
    assert service.get_run(run['id'])['status']=='cancelled'


def test_recovery_preserves_authenticated_terminal_success(tmp_path):
    from taskconsole.workflows_n8n import recover_interrupted
    service=svc(tmp_path);wf=service.save({'name':'Finished','nodes':[{'id':'a','kind':'python','source':'def main(inputs): return {}','inputs':{},'config':{}}],'edges':[]});service.publish(wf['id']);run=service.admit(wf['id'])
    service.execute_node(run['id'],'a');service.finish(run['id'])
    with service.store.transaction() as tx:
        current=tx.get('workflow_run',run['id']);current['adapter_lease']='test-lease';tx.put('workflow_run',current)
    recover_interrupted(service)
    restored=service.get_run(run['id'])
    assert restored['status']=='succeeded' and restored['nodes']['a']['status']=='succeeded'
    assert restored.get('adapter_finished_at')


def test_duplicate_callback_never_emits_running_completion_marker(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from fastapi.testclient import TestClient
    app=FastAPI();service=register_workflow_routes(app,Store(tmp_path,'sqlite:///'+str(tmp_path/'state.sqlite')),lambda request,tx,admin=False:{'id':'test','role':'admin'})
    graph={'name':'Slow','nodes':[{'id':'a','kind':'python','source':'import time\ndef main(inputs):\n time.sleep(0.4)\n return {"x":7}','inputs':{},'config':{}}],'edges':[]}
    wf=service.save(graph);service.publish(wf['id']);run=service.admit(wf['id'])
    endpoint=f'/internal/workflows/{run["id"]}/nodes/a';headers={'x-workflow-token':run['callback_token']}
    with TestClient(app) as client,ThreadPoolExecutor(2) as executor:
        first=executor.submit(client.post,endpoint,headers=headers)
        until=time.time()+3
        while service.get_run(run['id'])['nodes']['a']['status']!='running' and time.time()<until:time.sleep(.01)
        second=executor.submit(client.post,endpoint,headers=headers)
        result=second.result(timeout=5)
        assert result.status_code==200 and result.json()['status']=='succeeded'
        assert first.result(timeout=5).json()['status']=='succeeded'
    assert len(service.get_run(run['id'])['nodes']['a']['attempts'])==1


@pytest.mark.parametrize('available',[True,False])
def test_worker_heartbeat_and_stopped_state_keep_instance_generation(tmp_path,monkeypatch,available):
    from taskconsole import workflows_n8n as adapter
    service=svc(tmp_path);handlers={};heartbeats=[]
    monkeypatch.setenv('SLEEP_IN_INSTANCE_ID','synthetic-worker-generation')
    monkeypatch.setattr(adapter.signal,'signal',lambda signum,handler:handlers.__setitem__(signum,handler))
    def resolve_command():
        if not available:raise ValueError('n8n unavailable in this test')
        return ['/usr/bin/true']
    monkeypatch.setattr(adapter,'command',resolve_command)
    def one_tick():
        with service.store.transaction() as tx:heartbeats.append(tx.get('meta','workflow_worker'))
        handlers[adapter.signal.SIGTERM](None,None)
    monkeypatch.setattr(service,'tick',one_tick)
    adapter.worker_loop(service)
    assert heartbeats[0]['instance_id']=='synthetic-worker-generation'
    assert heartbeats[0]['status']==('ready' if available else 'unavailable')
    with service.store.transaction() as tx:stopped=tx.get('meta','workflow_worker')
    assert stopped['status']=='stopped'
    assert stopped['instance_id']=='synthetic-worker-generation'
