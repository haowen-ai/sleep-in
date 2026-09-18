"""Exact fixed-design data oracles; isolated workers and opt-in actual n8n."""
import copy
import hashlib
import json
import os
import shutil
from pathlib import Path

import pytest
from taskconsole.workflows_runtime import check_schema, resolve_path, run_script
from test_workflow_sql_contract import receiver
from taskconsole.workflows_sql import execute_sql

ORDERS = [
    {'order_id': 'A001', 'amount': '10.50', 'region': '华东'},
    {'order_id': 'A002', 'amount': '20.25', 'region': None},
    {'order_id': 'A003', 'amount': '0.00', 'region': '西部'},
]

@pytest.fixture(autouse=True)
def configured_node(monkeypatch):
    candidate = os.environ.get('SLEEP_IN_NODE') or os.environ.get('NODE') or shutil.which('node')
    if candidate:
        monkeypatch.setenv('SLEEP_IN_NODE', candidate)


@pytest.fixture
def live(tmp_path, monkeypatch):
    import collections,socket,threading,time,uvicorn,httpx
    from taskconsole.app import create_app
    from taskconsole.workflows import WorkflowService
    import taskconsole.workflows as workflows
    monkeypatch.delenv('SLEEP_IN_LOCAL',raising=False)
    app=create_app(tmp_path,'sqlite:///'+str(tmp_path/'state.sqlite'))
    svc=WorkflowService(app.state.store)
    calls=collections.Counter();original=workflows.execute_sql
    def count_sql(store,node,*args,**kwargs):
        calls[node['id']]+=1
        return original(store,node,*args,**kwargs)
    monkeypatch.setattr(workflows,'execute_sql',count_sql)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    deadline=time.monotonic()+10
    while not server.started and time.monotonic()<deadline:time.sleep(.05)
    assert server.started
    base=f'http://127.0.0.1:{port}'
    monkeypatch.setenv('SLEEP_IN_BASE_URL',base)
    with httpx.Client(base_url=base,timeout=60) as client:
        setup=client.post('/api/setup',json={'token':(tmp_path/'setup-token').read_text().strip(),'username':'owner','password':'acceptance owner password','timezone':'UTC','locale':'en'})
        assert setup.status_code==200,setup.text
        client.headers['X-CSRF-Token']=setup.json()['csrf']
        svc.acceptance_client=client;svc.acceptance_sql_calls=calls
        try:yield svc
        finally:server.should_exit=True;thread.join(timeout=10)


def worker(tmp_path, source, *, language='python', file=False, outputs=None):
    node = {'id': 'oracle', 'kind': language, 'source': source,
            'config': {'entry_mode': 'file' if file else 'helper'}, 'outputs': outputs or {}}
    return run_script(node, {}, tmp_path / 'attempt', tmp_path)


@pytest.mark.parametrize('language', ['python', 'javascript'])
@pytest.mark.parametrize('business', [{'x': 7}, {'schemaVersion': 42, 'label': 'business'},
                                    {'data': {'x': 7}, 'artifacts': ['business'], 'schemaVersion': 2}])
def test_helper_wraps_exact_business_data(tmp_path, language, business):
    source = ('def main(inputs): return ' + repr(business)) if language == 'python' else ('function main(inputs){return ' + json.dumps(business) + '}')
    result = worker(tmp_path, source, language=language)
    assert result['status'] == 'succeeded'
    assert result['output'] == {'schemaVersion': 1, 'data': business, 'artifacts': []}


@pytest.mark.parametrize('language', ['python', 'javascript'])
def test_file_entrypoint_retains_exact_envelope(tmp_path, language):
    envelope = {'schemaVersion': 1, 'data': {'x': 7}, 'artifacts': []}
    source = 'import os,json\njson.dump(' + repr(envelope) + ',open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))' if language == 'python' else 'require("fs").writeFileSync(process.env.SLEEP_IN_OUTPUT_FILE,' + json.dumps(json.dumps(envelope)) + ')'
    assert worker(tmp_path, source, language=language, file=True)['output'] == envelope


@pytest.mark.parametrize('envelope', [
    {'schemaVersion': 1, 'data': [], 'artifacts': []},
    {'schemaVersion': 1, 'data': 'x', 'artifacts': []},
    {'schemaVersion': 1, 'data': None, 'artifacts': []},
    {'data': {}, 'artifacts': []},
    {'schemaVersion': 2, 'data': {}, 'artifacts': []},
    {'schemaVersion': 1, 'data': {}, 'artifacts': {}},
])
def test_invalid_file_envelopes_rejected(tmp_path, envelope):
    source = 'import os,json\njson.dump(' + repr(envelope) + ',open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))'
    with pytest.raises(ValueError, match='version 1 envelope'):
        worker(tmp_path, source, file=True)


def test_stdout_never_becomes_structured_data(tmp_path):
    result = worker(tmp_path, 'def main(inputs):\n print("NOT JSON")\n print(\'{"x":999}\')\n return {"x":7}')
    assert result['output']['data'] == {'x': 7}
    assert 'NOT JSON' in result['stdout'] and '{"x":999}' in result['stdout']


@pytest.mark.parametrize('value,schema,error', [
    ({}, {'type':'object', 'required':['nil']}, 'missing required nil'),
    (True, {'type':'integer'}, 'expected integer'),
    (False, {'type':'number'}, 'expected number'),
    ([1, '2'], {'type':'array','items':{'type':'integer'}}, r'\[1\]'),
])
def test_schema_absence_boolean_and_nested_index(value, schema, error):
    with pytest.raises(ValueError, match=error):
        check_schema(value, schema)


@pytest.mark.parametrize('value', [None, False, 0, '', [], {}])
def test_present_values_and_nullable_schema_cross_worker(tmp_path, value):
    check_schema({'v':value}, {'type':'object','required':['v']})
    if value is None:
        check_schema(value, {'type':['string','null']})
    result = worker(tmp_path, 'def main(inputs): return ' + repr({'v':value}))
    assert result['status'] == 'succeeded' and result['output']['data'] == {'v':value}


def test_array_token_paths_and_out_of_range_policy():
    payload = {'rows':[{'id':1},{'id':2}], 'a.b':7, 'a':{'b':8}, '0':9}
    assert resolve_path(payload, ['rows',1,'id']) == resolve_path(payload, 'rows.1.id') == 2
    assert resolve_path(payload, ['rows']) == [{'id':1},{'id':2}]
    assert [resolve_path(payload,p) for p in [['a.b'],['a','b'],['0']]] == [7,8,9]
    for path in [['rows',-1,'id'], 'rows.-1.id']:
        with pytest.raises(ValueError): resolve_path(payload,path)
    with pytest.raises((KeyError,IndexError)): resolve_path(payload,['rows',2,'id'])


def test_sql_exact_bound_null_missing_identifier_and_semicolon(receiver):
    store,node = receiver
    node['source']='SELECT order_id,amount,region FROM orders ORDER BY order_id'
    assert execute_sql(store,node,{},'wf')['output']['data']['rows'] == ORDERS
    node['source']='SELECT order_id,amount,region FROM orders WHERE order_id=:order_id'
    assert execute_sql(store,node,{'order_id':'A002'},'wf')['output']['data']['rows'] == [ORDERS[1]]
    with pytest.raises(Exception): execute_sql(store,node,{},'wf')
    node['source']='SELECT order_id FROM orders WHERE region IS :region'
    assert execute_sql(store,node,{'region':None},'wf')['output']['data']['rows'] == [{'order_id':'A002'}]
    node['source']='SELECT * FROM :table'
    with pytest.raises(Exception): execute_sql(store,node,{'table':'orders'},'wf')
    writer = {**node, 'source':"UPDATE orders SET region='bad' WHERE order_id='A001'; DELETE FROM orders", 'config':{**node['config'],'mode':'write'}}
    with pytest.raises(Exception): execute_sql(store,writer,{},'wf')
    node['source']='SELECT order_id,amount,region FROM orders ORDER BY order_id'
    assert execute_sql(store,node,{},'wf')['output']['data']['rows'] == ORDERS
    writer['source']='UPDATE orders SET region=:region WHERE order_id=:id'
    assert execute_sql(store,writer,{'region':'a;b','id':'A001'},'wf')['output']['data']['affectedRows'] == 1
    assert execute_sql(store,node,{},'wf')['output']['data']['rows'][0]['region'] == 'a;b'


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'), reason='BLOCKED_ENV: actual pinned n8n required')
@pytest.mark.parametrize('variant', ['baseline','empty','renamed','constant','order_only','debug','draft_edit'])
def test_actual_n8n_golden_variants(live, variant):
    from taskconsole.workflows_n8n import execute_graph
    svc = live
    graph = svc.templates()[0]
    expected_orders = copy.deepcopy(ORDERS)
    count, total = 3, '30.75'
    if variant == 'empty':
        graph['nodes'][0]['source'] = 'SELECT order_id,amount,region FROM orders WHERE 1=0'
        expected_orders, count, total = [], 0, '0.00'
    if variant == 'constant':
        graph['nodes'][2]['inputs'] = {'summary':{'source':'constant','value':{'count':99,'total':'1.00'}}}
        count, total = 99, '1.00'
    if variant == 'order_only':
        graph['nodes'][1]['inputs'] = {}
        graph['nodes'][1]['source'] = 'def main(inputs):\n    assert inputs == {}\n    return {"summary":{"count":42,"total":"2.00"}}'
        count, total = 42, '2.00'
    if variant == 'debug':
        graph['nodes'][1]['source'] = 'print("NOT JSON\\nDEBUG TABLE")\n' + graph['nodes'][1]['source']
    if variant == 'renamed':
        for node in graph['nodes']: node['name'] = 'Renamed ' + node['id']
        graph['nodes'].reverse()
    for node in graph['nodes']:
        if node['kind']=='python':
            node['source']=node['source'].replace('def main(inputs):','def main(inputs):\n    from pathlib import Path\n    Path("business-calls").open("a").write("1\\n")')
        elif node['kind']=='javascript':node['source']='require("fs").appendFileSync("business-calls","1\\n");\n'+node['source']
    client=svc.acceptance_client
    response=client.post('/api/workflows',json=graph);assert response.status_code==200,response.text
    wf=response.json()
    response=client.post('/api/workflows/'+wf['id']+'/publish');assert response.status_code==200,response.text
    publication=response.json()
    response=client.post('/api/workflows/'+wf['id']+'/run',json={});assert response.status_code==202,response.text
    run=response.json()
    if variant == 'draft_edit':
        changed = copy.deepcopy(wf)
        changed['nodes'][0]['source'] = 'SELECT order_id,amount,region FROM orders WHERE 1=0'
        response=client.put('/api/workflows/'+wf['id'],json=changed);assert response.status_code==200,response.text
    execute_graph(svc,run['id'])
    result = svc.get_run(run['id'])
    assert result['status'] == 'succeeded', (result.get('error'),result.get('adapter_log'))
    assert result['version_id'] == publication['version_id']
    assert svc.acceptance_sql_calls['orders']==1
    for nid in ['summary','report']:
        counter=svc.store.path/'workflow-runs'/run['id']/nid/'attempt-1'/'project'/'business-calls'
        assert counter.read_text()=='1\n'
    assert result.get('n8n_execution_id') and result.get('graph_execution_id')
    assert all(n['status']=='succeeded' and len(n['attempts'])==1 for n in result['nodes'].values())
    assert result['nodes']['orders']['finished_at'] <= result['nodes']['summary']['started_at']
    assert result['nodes']['summary']['finished_at'] <= result['nodes']['report']['started_at']
    assert result['nodes']['orders']['output']['data']['rows'] == expected_orders
    assert result['nodes']['orders']['output']['data']['rowCount'] == len(expected_orders)
    if expected_orders:
        assert result['nodes']['orders']['output']['data']['columns']==[{'name':'order_id','type':'string','nullable':False},{'name':'amount','type':'string','nullable':False},{'name':'region','type':'string','nullable':True}]
    assert result['nodes']['summary']['inputs'] == ({} if variant=='order_only' else {'orders':expected_orders})
    assert result['nodes']['summary']['output']['data'] == {'summary':{'count':42 if variant=='order_only' else len(expected_orders),'total':'2.00' if variant=='order_only' else '0.00' if variant=='empty' else '30.75'}}
    assert result['nodes']['report']['inputs'] == {'summary':{'count':count,'total':total}}
    assert result['nodes']['report']['output']['data'] == {'message':f'{count} orders • {total}'}
    artifact = result['artifacts'][0]
    data = f'{count} orders • {total}\n'.encode()
    assert Path(artifact['path']).read_bytes() == data
    assert artifact['size']==len(data) and artifact['sha256']==hashlib.sha256(data).hexdigest()
    downloaded=client.get(f'/api/workflow-runs/{run["id"]}/artifacts/{artifact["id"]}')
    assert downloaded.status_code==200 and downloaded.content==data
    assert downloaded.headers['content-type'].startswith('text/plain') and 'report.txt' in downloaded.headers['content-disposition']
    if variant=='debug': assert 'NOT JSON' in result['nodes']['summary']['stdout']
