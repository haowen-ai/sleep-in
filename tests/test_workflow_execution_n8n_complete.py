"""Acceptance against installed n8n, isolated native persistence and local workers."""
import os
import socket
import threading
import time
from pathlib import Path
import pytest
from fastapi import FastAPI
from taskconsole.store import Store
from taskconsole.workflows import register_workflow_routes

pytestmark=pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='BLOCKED_ENV: actual native n8n command required')

@pytest.fixture
def live(tmp_path,monkeypatch):
    import uvicorn
    app=FastAPI();store=Store(tmp_path,'sqlite:///'+str(tmp_path/'state.sqlite'))
    svc=register_workflow_routes(app,store,lambda request,tx,admin=False:{'id':'test','role':'admin'})
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    deadline=time.monotonic()+10
    while not server.started and time.monotonic()<deadline:time.sleep(.05)
    assert server.started
    monkeypatch.setenv('SLEEP_IN_BASE_URL',f'http://127.0.0.1:{port}')
    try:yield svc
    finally:server.should_exit=True;thread.join(timeout=10)


def test_actual_n8n_retry_artifact_and_complete_large_mapping(live):
    from taskconsole.workflows_n8n import execute_graph
    svc=live
    source='''import os,json,pathlib
if "attempt-1" in os.environ["SLEEP_IN_INPUT_FILE"]: raise RuntimeError("synthetic safe retry")
pathlib.Path(os.environ["SLEEP_IN_ARTIFACT_DIR"],"note.txt").write_text("complete artifact")
json.dump({"schemaVersion":1,"data":{"rows":["x"*100 for _ in range(12000)]},"artifacts":[{"name":"note.txt"}]},open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))
'''
    graph={'name':'Actual retry and artifacts','timeout':90,'nodes':[
        {'id':'produce','kind':'python','source':source,'config':{'entry_mode':'file','retry':{'max_attempts':2,'safe_to_retry':True,'delay_seconds':.1}},'inputs':{}},
        {'id':'consume','kind':'python','source':'from pathlib import Path\ndef main(inputs): return {"count":len(inputs["rows"]),"text":Path(inputs["file"]["path"]).read_text()}','config':{},'inputs':{'rows':{'source':'node','node_id':'produce','path':'rows'},'file':{'source':'artifact','node_id':'produce','name':'note.txt'}}}
    ],'edges':[{'source':'produce','target':'consume'}]}
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id']);execute_graph(svc,run['id']);result=svc.get_run(run['id'])
    assert result['status']=='succeeded',(result.get('error'),result.get('adapter_log'))
    assert [a['status'] for a in result['nodes']['produce']['attempts']]==['failed','succeeded']
    assert result['nodes']['consume']['output']['data']=={'count':12000,'text':'complete artifact'}
    assert result['nodes']['produce']['output']['data_ref']['size']>1024*1024
    assert result.get('n8n_execution_id') and result.get('graph_execution_id')


def test_actual_n8n_cancel_waiting_process_prevents_downstream(live):
    from taskconsole.workflows_n8n import execute_graph
    svc=live
    graph={'name':'Actual cancellation','timeout':90,'nodes':[
        {'id':'slow','kind':'python','source':'import time\ndef main(inputs):\n time.sleep(45)\n return {}','inputs':{},'config':{}},
        {'id':'never','kind':'python','source':'def main(inputs): raise RuntimeError("must not run")','inputs':{},'config':{}}
    ],'edges':[{'source':'slow','target':'never'}]}
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id']);thread=threading.Thread(target=execute_graph,args=(svc,run['id']));thread.start()
    until=time.monotonic()+35
    while svc.get_run(run['id'])['nodes']['slow']['status']!='running' and time.monotonic()<until:time.sleep(.05)
    assert svc.get_run(run['id'])['nodes']['slow']['status']=='running'
    svc.cancel(run['id']);thread.join(timeout=10)
    assert not thread.is_alive()
    result=svc.get_run(run['id']);assert result['status']=='cancelled',result
    assert result['nodes']['slow']['status']=='cancelled'
    assert result['nodes']['never']['attempts']==[] and result['nodes']['never']['status']=='cancelled'


def test_actual_n8n_independent_roots_overlap(live):
    from taskconsole.workflows_n8n import execute_graph
    svc=live;source='import time\ndef main(inputs):\n start=time.monotonic()\n time.sleep(2)\n return {"start":start,"end":time.monotonic()}'
    graph={'name':'Actual concurrency','timeout':90,'nodes':[{'id':nid,'kind':'python','source':source,'inputs':{},'config':{}} for nid in ('a','b')],'edges':[]}
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id']);execute_graph(svc,run['id']);result=svc.get_run(run['id'])
    assert result['status']=='succeeded',result.get('adapter_log')
    a=result['nodes']['a']['output']['data'];b=result['nodes']['b']['output']['data']
    assert max(a['start'],b['start'])<min(a['end'],b['end']),'Independent root processes did not overlap'
    assert min(a['end']-a['start'],b['end']-b['start'])>=2
    parallel_window=max(a['end'],b['end'])-min(a['start'],b['start'])
    graph['name']='Actual serial baseline';graph['edges']=[{'source':'a','target':'b'}]
    serial=svc.save(graph);svc.publish(serial['id']);baseline=svc.admit(serial['id']);execute_graph(svc,baseline['id'])
    baseline=svc.get_run(baseline['id']);assert baseline['status']=='succeeded',baseline.get('adapter_log')
    left=baseline['nodes']['a']['output']['data'];right=baseline['nodes']['b']['output']['data']
    assert right['start']>=left['end']
    serial_window=right['end']-left['start']
    assert serial_window>=4 and parallel_window<serial_window,(parallel_window,serial_window)
    for recorded in (result,baseline):
        assert recorded['n8n_execution_id'] and recorded['graph_execution_id']
        assert all(len(node['attempts'])==1 for node in recorded['nodes'].values())
