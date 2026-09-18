"""Additional exact acceptance boundaries using real auth, workers and n8n."""
import json
import os
from pathlib import Path
import pytest
from test_acceptance_data import live, configured_node


def node(nid,source='def main(inputs): return inputs',**kwargs):
    return {'id':nid,'kind':'python','source':source,'inputs':{},'config':{},**kwargs}


def execute(live,nodes,edges,params=None):
    from taskconsole.workflows_n8n import execute_graph
    client=live.acceptance_client
    response=client.post('/api/workflows',json={'name':'Data edge acceptance','timeout':180,'nodes':nodes,'edges':edges})
    assert response.status_code==200,response.text
    wid=response.json()['id']
    response=client.post('/api/workflows/'+wid+'/publish');assert response.status_code==200,response.text
    response=client.post('/api/workflows/'+wid+'/run',json={'params':params or {}});assert response.status_code==202,response.text
    rid=response.json()['id'];execute_graph(live,rid);run=live.get_run(rid)
    assert run.get('n8n_execution_id') and run.get('graph_execution_id'),run
    return run


@pytest.mark.parametrize('extra',[{'and':[{'path':'x','operator':'eq','value':False}]},{'or':[]},{'conditions':[]},{'operator2':'eq'}])
def test_compound_condition_rejected_at_authenticated_publication(live,extra):
    client=live.acceptance_client
    graph={'name':'Compound condition forbidden','nodes':[node('a'),node('b')], 'edges':[{'source':'a','target':'b','condition':{'path':'x','operator':'eq','value':True,**extra}}]}
    saved=client.post('/api/workflows',json=graph);assert saved.status_code==200,saved.text
    errors=saved.json()['validation_errors']
    assert any('condition' in e['message'].lower() and e.get('node_id')=='a' for e in errors),errors
    published=client.post('/api/workflows/'+saved.json()['id']+'/publish')
    assert published.status_code==422,published.text
    assert published.json()['detail']['node_id']=='a'
    assert client.get('/api/workflow-runs').json()==[]


@pytest.mark.parametrize('operator', [[], {}, ['eq'], {'name':'eq'}])
def test_invalid_condition_operator_has_node_error_not_server_failure(live, operator):
    client=live.acceptance_client
    graph={'name':'Invalid operator','nodes':[node('a'),node('b')],
           'edges':[{'source':'a','target':'b','condition':{'path':'x','operator':operator,'value':True}}]}
    saved=client.post('/api/workflows',json=graph)
    assert saved.status_code==200,saved.text
    assert any(e['node_id']=='a' and 'condition operator' in e['message'].lower() for e in saved.json()['validation_errors'])
    published=client.post('/api/workflows/'+saved.json()['id']+'/publish')
    assert published.status_code==422 and published.json()['detail']['node_id']=='a'
    assert client.get('/api/workflow-runs').json()==[]


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='BLOCKED_ENV: actual pinned n8n required')
def test_actual_n8n_invalid_outputs_block_every_required_descendant(live):
    cases={
        'absent':('pass',{'required':['x']}),
        'exit_no_output':('print("exit-one-log");raise SystemExit(1)',{}),
        'python_exception':('raise RuntimeError("named-python-exception")',{}),
        'partial':('open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w").write("{")',{}),
        'utf8':('open(os.environ["SLEEP_IN_OUTPUT_FILE"],"wb").write(bytes([255]))',{}),
        'nan':('open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w").write(\'{"schemaVersion":1,"data":{"x":NaN},"artifacts":[]}\')',{}),
        'infinity':('open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w").write(\'{"schemaVersion":1,"data":{"x":Infinity},"artifacts":[]}\')',{}),
        'negative_infinity':('open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w").write(\'{"schemaVersion":1,"data":{"x":-Infinity},"artifacts":[]}\')',{}),
        'exit_one':('json.dump({"schemaVersion":1,"data":{"x":7},"artifacts":[]},open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"));raise SystemExit(1)',{}),
        'schema':('json.dump({"schemaVersion":1,"data":{"x":"bad"},"artifacts":[]},open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))',{'properties':{'x':{'type':'integer'}}}),
        'missing_artifact':('json.dump({"schemaVersion":1,"data":{},"artifacts":[{"name":"missing.bin"}]},open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))',{}),
    }
    nodes=[];edges=[]
    for nid,(body,schema) in cases.items():
        source='import os,json,pathlib\npathlib.Path("business-call").write_text("once")\nprint("worker-output-diagnostic")\n'+body
        nodes += [node(nid,source,config={'entry_mode':'file'},outputs=schema),node(nid+'_child','from pathlib import Path\ndef main(inputs):\n Path("forbidden-effect").write_text("effect-001")\n return {}')]
        edges.append({'source':nid,'target':nid+'_child'})
    nodes += [node('js_rejection','async function main(inputs){throw Error("named-js-rejection")}',kind='javascript'),node('js_rejection_child')]
    edges.append({'source':'js_rejection','target':'js_rejection_child'})
    run=execute(live,nodes,edges)
    assert run['status']=='failed'
    for nid in cases:
        source=run['nodes'][nid];child=run['nodes'][nid+'_child']
        assert source['status']=='failed' and len(source['attempts'])==1 and source['error']
        assert 'worker-output-diagnostic' in source['stdout']
        root=live.store.path/'workflow-runs'/run['id']
        assert (root/nid/'attempt-1'/'project'/'business-call').read_text()=='once'
        assert child['status']=='not_run' and child['reason']=='blocked_by_failed_dependency' and child['attempts']==[]
        assert not (root/(nid+'_child')/'attempt-1'/'project'/'forbidden-effect').exists()
    assert 'Process exited 1' in run['nodes']['exit_no_output']['error']
    assert 'exit-one-log' in run['nodes']['exit_no_output']['stdout']
    assert 'named-python-exception' in run['nodes']['python_exception']['stderr']
    assert run['nodes']['js_rejection']['status']=='failed' and 'named-js-rejection' in run['nodes']['js_rejection']['stderr']
    assert run['nodes']['js_rejection_child']['status']=='not_run' and run['nodes']['js_rejection_child']['attempts']==[]


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='BLOCKED_ENV: actual pinned n8n required')
def test_actual_n8n_no_output_order_only_chain_succeeds(live):
    run=execute(live,[node('no_output','print("ordinary order-only output")',config={'entry_mode':'file'}),node('after','def main(inputs):\n assert inputs=={}\n return {"ran":True}')],[{'source':'no_output','target':'after'}])
    assert run['status']=='succeeded'
    assert run['nodes']['no_output']['output']=={'schemaVersion':1,'data':{},'artifacts':[]}
    assert run['nodes']['after']['inputs']=={} and run['nodes']['after']['output']['data']=={'ran':True}
    assert all(len(n['attempts'])==1 for n in run['nodes'].values())


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='BLOCKED_ENV: actual pinned n8n required')
def test_actual_n8n_condition_types_and_missing_paths_never_coerce(live):
    cases=[('gt6',6,'gt',7,'skipped'),('gt7',7,'gt',7,'skipped'),('gt8',8,'gt',7,'succeeded'),
           ('true',True,'truthy',None,'succeeded'),('false',False,'truthy',None,'skipped'),
           ('null',None,'truthy',None,'failed'),('text','x','truthy',None,'failed'),('object',{},'truthy',None,'failed'),
           ('zero',0,'truthy',None,'failed'),('one',1,'truthy',None,'failed'),('mismatch',7,'eq','7','failed'),
           ('missing',None,'eq',7,'failed')]
    nodes=[];edges=[]
    for nid,value,op,expected,status in cases:
        nodes += [node(nid,'def main(inputs): return '+repr({} if nid=='missing' else {'x':value})),node(nid+'_edge','from pathlib import Path\ndef main(inputs):\n Path("edge-call").write_text("once")\n return {"active":True}'),node(nid+'_descendant')]
        edges += [{'source':nid,'target':nid+'_edge','condition':{'path':['x'],'operator':op,'value':expected}}, {'source':nid+'_edge','target':nid+'_descendant'}]
    run=execute(live,nodes,edges)
    assert run['status']=='failed'
    for nid,value,op,expected,status in cases:
        state=run['nodes'][nid+'_edge'];down=run['nodes'][nid+'_descendant']
        assert state['status']==status,(nid,state)
        call=live.store.path/'workflow-runs'/run['id']/(nid+'_edge')/'attempt-1'/'project'/'edge-call'
        if status=='succeeded':assert call.read_text()=='once' and down['status']=='succeeded'
        else:
            assert not call.exists() and state['attempts']==[] and down['attempts']==[]
            assert down['status']==('not_run' if status=='failed' else 'skipped')
            if status=='failed':assert 'Condition:' in state['error'] and down['reason']=='blocked_by_failed_dependency'


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='BLOCKED_ENV: actual pinned n8n required')
def test_actual_n8n_mapping_absence_defaults_types_and_input_copy(live):
    nodes=[node('source','def main(inputs): return {"x":None,"text":"7","rows":[1,2]}'),node('empty_source','def main(inputs): return {}')]
    bindings={
        'nullable':{'v':{'source':'node','node_id':'source','path':['x'],'optional':True,'default':9,'type':['integer','null']}},
        'missing_optional':{'v':{'source':'node','node_id':'empty_source','path':['x'],'optional':True,'default':9}},
        'missing_required':{'v':{'source':'node','node_id':'empty_source','path':['x']}},
        'wrong_type':{'v':{'source':'node','node_id':'source','path':['text'],'type':'number'}},
        'null_integer':{'v':{'source':'node','node_id':'source','path':['x'],'type':'integer'}},
        'parameter':{'name':{'source':'parameter','path':['customer','name']}},
        'mutate':{'a':{'source':'constant','value':{'a':[1,2]}},'rows':{'source':'node','node_id':'source','path':['rows']}},
        'read':{'a':{'source':'constant','value':{'a':[1,2]}},'rows':{'source':'node','node_id':'source','path':['rows']}},
    }
    edges=[]
    for nid,inputs in bindings.items():
        source='from pathlib import Path\ndef main(inputs):\n Path("business-call").write_text("once")\n return inputs'
        if nid=='mutate':source='from pathlib import Path\ndef main(inputs):\n Path("business-call").write_text("once")\n inputs["a"]["a"][0]=999\n inputs["rows"][0]=999\n return inputs'
        nodes.append(node(nid,source,inputs=inputs));edges.append({'source':'empty_source' if nid in {'missing_optional','missing_required'} else 'source','target':nid})
    # Read must run after the mutation, while still selecting the original source.
    edges.append({'source':'mutate','target':'read'});nodes[-1]['config']={'join':'all'}
    run=execute(live,nodes,edges,{'customer':{'name':'Ada'}})
    assert run['status']=='failed'
    for nid in ['missing_required','wrong_type','null_integer']:
        state=run['nodes'][nid];assert state['status']=='failed' and state['attempts']==[] and not state.get('process_started')
        assert not (live.store.path/'workflow-runs'/run['id']/nid/'attempt-1'/'project'/'business-call').exists()
    for nid,expected in [('nullable',{'v':None}),('missing_optional',{'v':9}),('parameter',{'name':'Ada'}),('read',{'a':{'a':[1,2]},'rows':[1,2]})]:
        assert run['nodes'][nid]['inputs']==expected and run['nodes'][nid]['output']['data']==expected
    assert run['nodes']['source']['output']['data']['rows']==[1,2]
    assert run['nodes']['mutate']['inputs']=={'a':{'a':[1,2]},'rows':[1,2]}
    assert run['params']=={'customer':{'name':'Ada'}}
    assert run['nodes']['missing_optional']['input_provenance']['v']=={'source':'node','node_id':'empty_source','path':['x'],'default_used':True}
    detail=run['nodes']['missing_required']['error_detail']
    assert detail['node_id']=='missing_required' and detail['field']=='v'
    assert 'empty_source' in detail['message'] and '["x"]' in detail['message']


def test_output_parse_failure_retains_logs_without_credential_leak(live):
    secret='fixture-output-failure-private-value'
    cred=live.save_credential({'name':'Synthetic','value':secret})
    source='import os,json,sys\ni=json.load(open(os.environ["SLEEP_IN_INPUT_FILE"]))\nprint("stdout marker "+i["token"])\nprint("stderr marker "+i["token"],file=sys.stderr)\nopen(os.environ["SLEEP_IN_OUTPUT_FILE"],"w").write("{")'
    graph={'name':'Diagnostic retention','nodes':[node('a',source,config={'entry_mode':'file'},inputs={'token':{'source':'credential','credential_id':cred['id']}})],'edges':[]}
    wf=live.save(graph);live.publish(wf['id']);run=live.admit(wf['id']);live.execute_node(run['id'],'a');result=live.finish(run['id'])
    state=result['nodes']['a'];assert state['status']=='failed'
    assert 'stdout marker [redacted]' in state['stdout'] and 'stderr marker [redacted]' in state['stderr']
    assert state['attempts'][0]['stdout']==state['stdout'] and state['attempts'][0]['stderr']==state['stderr']
    assert secret not in json.dumps(result)


def test_mapping_default_provenance_and_source_path_error(live):
    graph={'name':'Provenance','nodes':[node('a','def main(inputs): return {}'),node('optional',inputs={'v':{'source':'node','node_id':'a','path':['nested','x'],'optional':True,'default':9}}),node('required',inputs={'v':{'source':'node','node_id':'a','path':['nested','x']}})],'edges':[{'source':'a','target':'optional'},{'source':'a','target':'required'}]}
    wf=live.save(graph);live.publish(wf['id']);run=live.admit(wf['id'])
    for nid in ['a','optional','required']:live.execute_node(run['id'],nid)
    result=live.finish(run['id'])
    provenance=result['nodes']['optional']['input_provenance']['v']
    assert provenance=={'source':'node','node_id':'a','path':['nested','x'],'default_used':True}
    required=result['nodes']['required']
    assert required['status']=='failed' and required['attempts']==[]
    assert required['error_detail']['node_id']=='required' and required['error_detail']['field']=='v'
    assert 'a' in required['error'] and 'nested' in required['error'] and 'x' in required['error']


def test_resume_from_node_is_explicitly_unsupported_without_admission(live):
    wf=live.save({'name':'No implicit replay','nodes':[node('write','def main(inputs): return {"effect":"would-write"}')],'edges':[]})
    live.publish(wf['id']);client=live.acceptance_client
    for key in ['resume','resume_from','resume_from_node','resume_node_id','failed_node']:
        response=client.post('/api/workflows/'+wf['id']+'/run',json={key:'write'})
        assert response.status_code==422,response.text
        assert response.json()['detail']['code']=='unsupported_resume'
        assert client.get('/api/workflow-runs').json()==[]


def test_postprocessing_storage_failure_preserves_worker_diagnostics(live, monkeypatch):
    import taskconsole.workflows as workflows
    wf=live.save({'name':'Collection fault','nodes':[node('a','def main(inputs):\n print("collection-diagnostic")\n return {"x":7}')],'edges':[]})
    live.publish(wf['id']);run=live.admit(wf['id'])
    def disk_fault(*args):
        raise OSError('Synthetic output storage full')
    monkeypatch.setattr(workflows,'spill_output',disk_fault)
    state=live.execute_node(run['id'],'a')
    assert state['status']=='failed' and 'storage full' in state['error']
    assert 'collection-diagnostic' in state['stdout']
    assert 'collection-diagnostic' in state['attempts'][0]['stdout']
    assert live.finish(run['id'])['status']=='failed'
