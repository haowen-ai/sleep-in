"""Exact mapping/API contracts and native seven-language chain acceptance."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest
from test_acceptance_data import live, configured_node
from test_acceptance_branches import native, mapping, run_graph, calls, requires_n8n
from test_runtime_language_matrix import fixture_program, FTYPES, LANGUAGES


def save(client,nodes,edges=()):
    response=client.post('/api/workflows',json={'name':'Mapping contracts','nodes':nodes,'edges':list(edges)})
    assert response.status_code==200,response.text
    return response.json()


def rejected(client,wf,nid,field=None,code=None,contains=None):
    assert wf['validation_errors']
    response=client.post('/api/workflows/'+wf['id']+'/publish')
    assert response.status_code==422,response.text
    detail=response.json()['detail']
    assert detail.get('node_id')==nid,detail
    if field is not None:assert detail.get('field')==field,detail
    if code:assert detail['code']==code,detail
    assert isinstance(detail['message'],str) and detail['message']
    if contains:assert contains.lower() in detail['message'].lower(),detail
    assert client.get('/api/workflow-runs').json()==[]
    assert not client.get('/api/workflows/'+wf['id']).json()['published_version_id']
    return detail


@pytest.mark.parametrize('variant',['disconnected','future','deleted'])
def test_authenticated_unreachable_or_deleted_binding_is_preserved_and_rejected(live,variant):
    client=live.acceptance_client
    a=native('A',{'x':7});b=native('B',body='return inputs',inputs={'v':mapping('A',path=['x'])})
    edges=[] if variant=='disconnected' else [{'source':'A','target':'B'}]
    if variant=='future':a['inputs']={'v':mapping('B',path=['x'])};b['inputs']={}
    wf=save(client,[a,b],edges)
    if variant=='deleted':
        wf['nodes']=[b];wf['edges']=[]
        response=client.put('/api/workflows/'+wf['id'],json=wf);assert response.status_code==200
        wf=response.json()
        assert wf['nodes'][0]['inputs']=={'v':mapping('A',path=['x'])}
        assert client.get('/api/workflows/'+wf['id']).json()['nodes'][0]['inputs']==b['inputs']
    rejected(client,wf,'A' if variant=='future' else 'B','v',code='missing_source' if variant=='deleted' else 'unreachable_source')


def test_authenticated_optional_default_requires_explicit_property_and_accepts_null(live):
    client=live.acceptance_client
    a=native('A',{})
    binding={'source':'node','node_id':'A','path':['x'],'optional':True,'type':['number','null']}
    b=native('B',inputs={'v':binding},body='return inputs');b['input_schema']={'type':'object','properties':{'v':{'type':['number','null']}}}
    wf=save(client,[a,b],[{'source':'A','target':'B'}]);rejected(client,wf,'B','v',contains='explicit default')
    wf['nodes'][1]['inputs']['v']['default']=None
    response=client.put('/api/workflows/'+wf['id'],json=wf);assert response.status_code==200 and response.json()['validation_errors']==[]
    assert client.post('/api/workflows/'+wf['id']+'/publish').status_code==200


@pytest.mark.parametrize('schema,value,keyword',[
    ({'type':'object','additionalProperties':False},{'extra':1},'additionalProperties'),
    ({'type':'string','enum':['a','b']},'c','enum'),
    ({'type':'number','minimum':0,'maximum':10},11,'maximum'),
])
def test_authenticated_unsupported_schema_keywords_reject_with_field(live,schema,value,keyword):
    client=live.acceptance_client;n=native('B',body='return inputs',inputs={'v':{'source':'constant','value':value}})
    n['input_schema']={'type':'object','properties':{'v':schema}}
    wf=save(client,[n]);rejected(client,wf,'B','v',contains=keyword)


@pytest.mark.parametrize('variant',['duplicate','missing'])
def test_authenticated_invalid_graph_ids_reject_before_compilation(live,variant):
    a=native('A',{'x':7});nodes=[a,copy.deepcopy(a)] if variant=='duplicate' else [a]
    edges=[] if variant=='duplicate' else [{'source':'A','target':'MISSING'}]
    wf=save(live.acceptance_client,nodes,edges)
    rejected(live.acceptance_client,wf,'A' if variant=='duplicate' else 'MISSING',contains='Duplicate' if variant=='duplicate' else 'missing')
    assert not (live.store.path/'workflow-runs').exists()


def test_authenticated_raw_duplicate_input_names_never_use_last_value(live):
    client=live.acceptance_client
    raw='{"name":"Duplicate binding","nodes":[{"id":"A","kind":"python","source":"def main(inputs): return inputs","config":{},"inputs":{"v":{"source":"constant","value":7},"v":{"source":"constant","value":8}}}],"edges":[]}'
    response=client.post('/api/workflows',content=raw,headers={'Content-Type':'application/json'})
    assert response.status_code in {400,422},response.text
    assert 'duplicate' in response.json()['detail']['message'].lower()
    assert client.get('/api/workflows').json()==[] and client.get('/api/workflow-runs').json()==[]


def test_authenticated_known_source_and_target_schema_mismatch_blocks_publication(live):
    a=native('A',{'x':'7'});a['outputs']={'type':'object','properties':{'x':{'type':'string'}}}
    b=native('B',inputs={'v':mapping('A',path=['x'])},body='return inputs')
    b['input_schema']={'type':'object','required':['v'],'properties':{'v':{'type':'number'}}}
    wf=save(live.acceptance_client,[a,b],[{'source':'A','target':'B'}])
    rejected(live.acceptance_client,wf,'B','v',contains='incompatible')


@requires_n8n
def test_actual_n8n_explicit_nonadjacent_renamed_paths_constants_and_dynamic_types(live):
    a=native('A',{'x':7,'a.b':7,'a':{'b':8},'0':9});a['name']='Renamed source display name'
    b=native('B',inputs={'v':mapping('A',path=['x'])},body='return inputs')
    c=native('C',inputs={'v':mapping('A',path=['x'])},body='return inputs')
    literal=native('literal',inputs={k:mapping('A',path=p) for k,p in [('dot',['a.b']),('nested',['a','b']),('zero',['0'])]},body='return inputs')
    numeric_key=native('numeric_key',inputs={'v':mapping('A',path=[0])},body='return inputs')
    constant={'source':'constant','value':{'a':[1,2]}}
    mutate=native('mutate',inputs={'obj':copy.deepcopy(constant)},body='inputs["obj"]["a"][0]=99\nreturn inputs')
    retain=native('retain',inputs={'obj':copy.deepcopy(constant)},body='return inputs')
    text=native('text',{'x':'7'})
    dynamic=native('dynamic',inputs={'v':mapping('text',path=['x'])},body='return inputs');dynamic['input_schema']={'type':'object','required':['v'],'properties':{'v':{'type':'number'}}}
    edges=[{'source':'A','target':'B'},{'source':'B','target':'C'},{'source':'A','target':'literal'},{'source':'A','target':'mutate'},{'source':'A','target':'retain'},{'source':'text','target':'dynamic'},{'source':'A','target':'numeric_key'}]
    run=run_graph(live,[a,b,c,literal,mutate,retain,text,dynamic,numeric_key],edges,'Exact data mappings')
    assert run['status']=='failed'
    for nid in ['A','B','C','literal','mutate','retain','text']:calls(live,run,nid,1);assert run['nodes'][nid]['status']=='succeeded'
    for nid in ['B','C']:assert run['nodes'][nid]['inputs']==run['nodes'][nid]['output']['data']=={'v':7}
    assert run['nodes']['B']['finished_at']<=run['nodes']['C']['started_at']
    assert run['snapshot']['nodes'][0]['id']=='A' and run['snapshot']['nodes'][0]['name']==a['name']
    assert run['nodes']['literal']['output']['data']=={'dot':7,'nested':8,'zero':9}
    assert run['nodes']['mutate']['output']['data']=={'obj':{'a':[99,2]}}
    assert run['nodes']['retain']['inputs']==run['nodes']['retain']['output']['data']=={'obj':{'a':[1,2]}}
    assert run['nodes']['mutate']['inputs']=={'obj':{'a':[1,2]}}
    for nid in ['mutate','retain']:assert next(n for n in run['snapshot']['nodes'] if n['id']==nid)['inputs']['obj']==constant
    calls(live,run,'numeric_key',0)
    assert run['nodes']['numeric_key']['status']=='failed'
    calls(live,run,'dynamic',0)
    assert run['nodes']['dynamic']['status']=='failed' and 'number' in run['nodes']['dynamic']['error']
    assert 'v' in run['nodes']['dynamic']['error']


@requires_n8n
def test_actual_n8n_seven_language_chain_retains_types_and_sql_bootstrap(live):
    from taskconsole.workflows_packs import RuntimePacks
    from taskconsole.workflows_sql import create_connection
    java=os.environ.get('SLEEP_IN_TEST_JAVAC') or shutil.which('javac')
    if not java:pytest.skip('BLOCKED_ENV: actual JDK required')
    connection=create_connection(live.store,{'name':'Chain bootstrap','dialect':'sqlite','config':{'synthetic':True}})
    sql={'id':'sql','kind':'sql','source':'SELECT 7 AS bootstrap','inputs':{},'config':{'dialect':'sqlite','connection_id':connection['id']}}
    nodes=[sql];edges=[];previous='sql';packs=RuntimePacks(live.store)
    for language in LANGUAGES[1:]:
        config={'executable':java} if language=='java' else {}
        profile=packs.create({'name':'Chain '+language,'language':language,'config':config});version=packs.build(profile['id'])
        assert version['status']=='ready',version
        source=fixture_program(language,'types')
        if language=='python':
            source=source.replace('result={"received":json.load(open(os.environ["SLEEP_IN_INPUT_FILE"]))["payload"]}', 'i=json.load(open(os.environ["SLEEP_IN_INPUT_FILE"]))\nassert i["bootstrap"]==[{"bootstrap":7}]\nresult={"received":i["payload"]}')
        n={'id':language,'kind':language,'source':source,'config':{'entry_mode':'file','runtime_version_id':version['id']},'inputs':{'payload':{'source':'constant','value':FTYPES} if language=='python' else mapping(previous,path=['received'])}}
        if language=='python':n['inputs']['bootstrap']=mapping('sql',path=['rows'])
        nodes.append(n);edges.append({'source':previous,'target':language});previous=language
    run=run_graph(live,nodes,edges,'Seven actual languages')
    assert run['status']=='succeeded',(run.get('error'),run['nodes'])
    assert live.acceptance_sql_calls['sql']==1 and run['nodes']['sql']['output']['data']['rows']==[{'bootstrap':7}]
    assert run['nodes']['python']['inputs']=={'payload':FTYPES,'bootstrap':[{'bootstrap':7}]}
    for language in LANGUAGES[1:]:
        state=run['nodes'][language];assert state['output']['data']=={'received':FTYPES}
        if language!='python':assert state['inputs']=={'payload':FTYPES}
        assert len(state['attempts'])==1
        counter=live.store.path/'workflow-runs'/run['id']/language/'attempt-1'/'project'/'business-calls'
        assert counter.read_text()=='1\n'
        assert next(n for n in run['snapshot']['nodes'] if n['id']==language)['kind']==language
    for edge in edges:assert run['nodes'][edge['source']]['finished_at']<=run['nodes'][edge['target']]['started_at']


@pytest.mark.parametrize('source_schema,allowed',[
    ({'type':'string'},False),({'type':'object'},True),({},True),
    ({'type':'object','properties':{'summary':{'type':'string'}}},False),
])
def test_known_scalar_mapping_path_rejects_but_unknown_schema_remains_dynamic(live,source_schema,allowed):
    a=native('A',{});a['outputs']=source_schema
    b=native('B',inputs={'v':mapping('A',path=['summary','count'])},body='return inputs')
    wf=save(live.acceptance_client,[a,b],[{'source':'A','target':'B'}])
    if allowed:
        assert wf['validation_errors']==[]
        assert live.acceptance_client.post('/api/workflows/'+wf['id']+'/publish').status_code==200
    else:rejected(live.acceptance_client,wf,'B','v',contains='path')


def test_duplicate_node_model_and_authenticated_save_preserve_independent_bindings(live):
    model=Path(__file__).parents[1]/'taskconsole/static/workflow-model.js'
    a=native('A',body='return inputs',inputs={'v':mapping('P',path=['x'])},config={'custom':{'a':[1,2]}})
    graph={'name':'Duplicate contract','nodes':[native('P',{'x':7}),a,native('B',inputs={'v':mapping('A',path=['v'])},body='return inputs')],'edges':[{'source':'P','target':'A','required':True},{'source':'A','target':'B'}]}
    code='''import fs from 'node:fs';const {GraphModel}=await import('data:text/javascript;base64,'+Buffer.from(fs.readFileSync(process.argv[1])).toString('base64'));let text='';for await(const c of process.stdin)text+=c;const g=new GraphModel(JSON.parse(text));const id=g.duplicate('A');const before=structuredClone(g.value);g.node(id).source='def main(inputs): return {"edited":True}';g.node(id).config.custom.a[0]=99;g.node(id).inputs.v.path[0]='changed';console.log(JSON.stringify({id,before,after:g.value}));'''
    node=os.environ.get('SLEEP_IN_NODE') or shutil.which('node')
    assert node,'Actual Node required for GraphModel duplication'
    result=subprocess.run([node,'--input-type=module','-e',code,str(model)],input=json.dumps(graph),text=True,capture_output=True,check=True)
    value=json.loads(result.stdout);dup=value['id'];assert dup not in {'P','A','B'}
    copied=next(n for n in value['before']['nodes'] if n['id']==dup)
    for key in ['source','config','inputs']:assert copied[key]==a[key]
    assert {'source':'P','target':dup,'required':True} in value['before']['edges']
    assert not any(e['source']==dup for e in value['before']['edges'])
    for state in [value['before'],value['after']]:
        saved=live.acceptance_client.post('/api/workflows',json=state);assert saved.status_code==200,saved.text
        persisted=live.acceptance_client.get('/api/workflows/'+saved.json()['id']).json()
        original=next(n for n in persisted['nodes'] if n['id']=='A')
        for key in ['source','config','inputs']:assert original[key]==a[key]
        assert next(n for n in persisted['nodes'] if n['id']=='B')['inputs']['v']['node_id']=='A'
    edited=next(n for n in persisted['nodes'] if n['id']==dup)
    assert edited['config']['custom']['a']==[99,2] and edited['inputs']['v']['path']==['changed']
    assert edited['source']=='def main(inputs): return {"edited":True}'


@pytest.mark.parametrize('kind',['missing_credential','unauthorized_credential','unknown_source'])
def test_authenticated_invalid_credential_or_source_never_publishes(live,kind):
    client=live.acceptance_client
    if kind=='unknown_source':binding={'source':'made-up-source','value':7}
    else:
        cid='missing'
        if kind=='unauthorized_credential':
            response=client.post('/api/workflow-credentials',json={'name':'Synthetic restricted','value':'not-a-real-secret','allowed_workflows':['another-workflow']});assert response.status_code==200
            cid=response.json()['id']
        binding={'source':'credential','credential_id':cid}
    wf=save(client,[native('A',inputs={'v':binding},body='return inputs')])
    rejected(client,wf,'A','v',contains='source' if kind=='unknown_source' else 'credential')


@requires_n8n
def test_actual_n8n_authorized_context_credential_and_missing_context(live):
    client=live.acceptance_client
    response=client.post('/api/workflow-credentials',json={'name':'Synthetic acceptance reference','value':'contract-credential-value'});assert response.status_code==200
    credential=response.json()
    good=native('authorized',inputs={**{key:{'source':'context','path':[key]} for key in ['run_id','workflow_id','version_id']},'secret':{'source':'credential','credential_id':credential['id']}},body='return {"run_id":inputs["run_id"],"workflow_id":inputs["workflow_id"],"version_id":inputs["version_id"],"resolved":inputs["secret"]=="contract-credential-value"}')
    bad=native('missing_context',inputs={'v':{'source':'context','path':['not_supported']}},body='return inputs')
    run=run_graph(live,[good,bad],[],'Context and authorized credential')
    assert run['status']=='failed'
    assert run['nodes']['authorized']['output']['data']=={'run_id':run['id'],'workflow_id':run['workflow_id'],'version_id':run['version_id'],'resolved':True}
    assert run['nodes']['authorized']['credential_revisions']=={credential['id']:credential['revision']}
    assert 'contract-credential-value' not in json.dumps(run['nodes']['authorized'])
    calls(live,run,'authorized',1);calls(live,run,'missing_context',0)
    assert run['nodes']['missing_context']['status']=='failed'
    assert run['nodes']['missing_context']['error_detail']['field']=='v'
    assert 'context' in run['nodes']['missing_context']['error'] and 'not_supported' in run['nodes']['missing_context']['error']


@pytest.mark.parametrize('location,schema',[
    ('source',{'type':42}),('source',{'type':{}}),('source',{'type':[{}]}),
    ('source',{'type':'object','properties':[]}),('target',{'type':'object','properties':[]}),
])
def test_malformed_declared_schema_is_node_error_not_server_failure(live,location,schema):
    a=native('A',{'x':7});b=native('B',inputs={'v':mapping('A',path=['x'])},body='return inputs')
    if location=='source':a['outputs']=schema
    else:b['input_schema']=schema
    wf=save(live.acceptance_client,[a,b],[{'source':'A','target':'B'}])
    rejected(live.acceptance_client,wf,'A' if location=='source' else 'B')


@pytest.mark.parametrize('container,path,target',[
    ({'type':['object','null'],'properties':{'x':{'type':'string'}}},['x'],{'type':'number'}),
    ({'type':['null','object'],'properties':{'x':{'type':'string'}}},['x','y'],{}),
    ({'type':['array','null'],'items':{'type':'string'}},[0],{'type':'number'}),
    ({'type':['null','array'],'items':{'type':'string'}},[0,'y'],{}),
])
def test_nullable_container_retains_known_path_and_type_constraints(live,container,path,target):
    a=native('A',{});a['outputs']=container
    b=native('B',inputs={'v':mapping('A',path=path)},body='return inputs');b['input_schema']={'properties':{'v':target}}
    wf=save(live.acceptance_client,[a,b],[{'source':'A','target':'B'}])
    rejected(live.acceptance_client,wf,'B','v',contains='incompatible' if target else 'path')


@pytest.mark.parametrize('field,value',[
    ('kind',[]),('kind',{}),('config',[]),('config',None),
    ('binding_source',[]),('binding_source',{}),
    ('join',[]),('join',{}),('merge',[]),('merge',{}),
])
def test_malformed_node_options_are_draft_errors_and_publication_rejections(live,field,value):
    n=native('A',{})
    if field in {'kind','config'}:n[field]=value
    elif field=='binding_source':n['inputs']={'v':{'source':value,'value':7}}
    else:n['config'][field]=value
    wf=save(live.acceptance_client,[n]);rejected(live.acceptance_client,wf,'A','v' if field=='binding_source' else None)


@pytest.mark.parametrize('config',[[],None])
def test_custom_node_nonobject_configuration_is_structured_validation_error(live,config):
    n=native('A',{});n['kind']='custom';n['config']=config
    wf=save(live.acceptance_client,[n])
    rejected(live.acceptance_client,wf,'A',contains='configuration must be an object')
