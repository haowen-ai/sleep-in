"""Fixed F-BRANCH oracles against genuine authenticated n8n and native workers."""
from concurrent.futures import ThreadPoolExecutor
import copy
import json
import os
from pathlib import Path
import time

import pytest
from test_acceptance_data import live, configured_node

requires_n8n=pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='BLOCKED_ENV: pinned actual n8n required')
B={'label':'B','values':[1,2]}
C={'label':'C','values':[3]}
SKIPPED={'marker':'skipped'}


def native(nid,data=None,*,inputs=None,config=None,fail=False,gate=None,body=None):
    lines=['from pathlib import Path','import time','def main(inputs):','    with Path("business-calls").open("a") as f: f.write("call\\n")']
    if gate:lines += ['    deadline=time.monotonic()+90',f'    while not Path({str(gate)!r}).exists():','        if time.monotonic()>deadline: raise RuntimeError("acceptance gate deadline")','        time.sleep(.02)']
    if fail:lines += ['    raise RuntimeError("intentional branch failure")']
    elif body:lines += ['    '+line for line in body.splitlines()]
    else:lines += ['    return '+repr(data if data is not None else {})]
    return {'id':nid,'kind':'python','source':'\n'.join(lines),'inputs':inputs or {},'config':config or {}}


def mapping(nid,default=None,path=None):
    result={'source':'node','node_id':nid,'path':[] if path is None else path}
    if default is not None:result.update(optional=True,default=copy.deepcopy(default))
    return result


def merged(nid,left,right,*,default=SKIPPED,flag=None,join='all'):
    body='sources={k:v for k,v in inputs.items() if isinstance(v,dict) and "label" in v}\nmarkers={k:(v.get("marker","succeeded") if isinstance(v,dict) else "default") for k,v in inputs.items()}\nreturn {"sources":sources,"markers":markers'+(',"flag":'+repr(flag) if flag is not None else '')+'}'
    return native(nid,inputs={'B':mapping(left,default),'C':mapping(right,default)},config={'join':join,'merge':'named'},body=body)


def admit(svc,nodes,edges,name):
    client=svc.acceptance_client
    response=client.post('/api/workflows',json={'name':name,'timeout':180,'nodes':nodes,'edges':edges})
    assert response.status_code==200,response.text
    wid=response.json()['id'];publication=client.post('/api/workflows/'+wid+'/publish')
    assert publication.status_code==200,publication.text
    response=client.post('/api/workflows/'+wid+'/run',json={});assert response.status_code==202,response.text
    run=svc.get_run(response.json()['id'])
    assert run['version_id']==publication.json()['version_id']
    return run


def run_graph(svc,nodes,edges,name):
    from taskconsole.workflows_n8n import execute_graph
    run=admit(svc,nodes,edges,name);execute_graph(svc,run['id'])
    result=svc.get_run(run['id'])
    assert result.get('n8n_execution_id') and result.get('graph_execution_id'),(result.get('error'),result.get('adapter_log'))
    return result


def calls(svc,run,nid,count):
    record=run['nodes'][nid]
    file=svc.store.path/'workflow-runs'/run['id']/nid/'attempt-1'/'project'/'business-calls'
    assert len(record['attempts'])==count,(nid,record)
    if count:assert file.read_text().splitlines()==['call']
    else:
        assert not file.exists() and not record.get('process_started')
        assert 'output' not in record


def after_both(run,join,predecessors):
    for nid in predecessors:
        assert run['nodes'][nid]['finished_at']<=run['nodes'][join]['started_at'],(nid,join)


def fbranch(scenario):
    flag=scenario not in {'false','skipped_optional'}
    a=native('A',{'flag':flag,'amount':7})
    b=native('B',B,fail=scenario in {'required_failure','optional_failure','both_failed'})
    c=native('C',C,fail=scenario=='both_failed')
    j=merged('J','B','C',join='any' if scenario=='both_failed' else 'all')
    edges=[{'source':'A','target':'B'},{'source':'A','target':'C'},{'source':'B','target':'J'},{'source':'C','target':'J'}]
    if scenario in {'true','false','none','skipped_optional'}:
        edges[0]['condition']={'path':['flag'],'operator':'eq','value':False if scenario=='none' else True}
        edges[1]['condition']={'path':['flag'],'operator':'eq','value':False}
    if scenario in {'optional_failure','skipped_optional'}:
        edges[2]['required']=False
        j['inputs']['B']['default']=[] if scenario=='optional_failure' else 0
        j['source']=native('J',body='return inputs')['source']
    return [a,b,c,j],edges


@requires_n8n
@pytest.mark.parametrize('scenario,states,status',[
    ('true',['succeeded','succeeded','skipped','succeeded'],'succeeded'),
    ('false',['succeeded','skipped','succeeded','succeeded'],'succeeded'),
    ('none',['succeeded','skipped','skipped','skipped'],'succeeded'),
    ('both',['succeeded','succeeded','succeeded','succeeded'],'succeeded'),
    ('required_failure',['succeeded','failed','succeeded','not_run'],'failed'),
    ('optional_failure',['succeeded','failed','succeeded','succeeded'],'partial'),
    ('skipped_optional',['succeeded','skipped','succeeded','succeeded'],'succeeded'),
    ('both_failed',['succeeded','failed','failed','not_run'],'failed'),
])
def test_actual_n8n_fbranch_named_merges_and_terminal_states(live,scenario,states,status):
    nodes,edges=fbranch(scenario)
    result=run_graph(live,nodes,edges,'F-BRANCH '+scenario)
    assert result['status']==status,(result.get('error'),result.get('adapter_log'))
    if scenario=='both':
        emitted=json.loads((live.store.path/'workflow-runs'/result['id']/'n8n-graph.json').read_text())
        barrier=next(n for n in emitted['nodes'] if n['name']=='barrier_submit_J_1')
        assert barrier['type']=='n8n-nodes-base.merge' and barrier['parameters']=={'mode':'append','numberInputs':2}
        for source,index in [('B',0),('C',1)]:
            assert emitted['connections']['node_'+source]['main'][0]==[{'node':barrier['name'],'type':'main','index':index}]
        assert emitted['connections'][barrier['name']]['main'][0]==[{'node':'submit_J','type':'main','index':0}]
        assert next(n for n in emitted['nodes'] if n['name']=='submit_J')['executeOnce'] is True
    for nid,state in zip(['A','B','C','J'],states):
        assert result['nodes'][nid]['status']==state,(nid,result['nodes'][nid])
        calls(live,result,nid,1 if state in {'succeeded','failed'} else 0)
    assert result['nodes']['A']['output']['data']=={'flag':scenario not in {'false','skipped_optional'},'amount':7}
    if result['nodes']['J']['status']=='succeeded':
        after_both(result,'J',['B','C'])
        if scenario in {'optional_failure','skipped_optional'}:
            expected={'B':[] if scenario=='optional_failure' else 0,'C':C}
            assert result['nodes']['J']['inputs']==expected
            assert result['nodes']['J']['output']['data']==expected
            assert result['nodes']['J']['input_provenance']['B']['default_used'] is True
        else:
            expected={'B':B if states[1]=='succeeded' else SKIPPED,'C':C if states[2]=='succeeded' else SKIPPED}
            assert result['nodes']['J']['inputs']==expected
            assert result['nodes']['J']['output']['data']=={
                'sources':{key:value for key,value in expected.items() if 'label' in value},
                'markers':{key:'succeeded' if 'label' in value else 'skipped' for key,value in expected.items()}}
    elif status=='failed':
        assert result['nodes']['J']['reason']=='blocked_by_failed_dependency'
    else:
        assert result['nodes']['J']['reason']=='unselected_branch'
        assert 'output' not in result['nodes']['J']


def gated_execution(svc,nodes,edges,gate,held,finished,assert_result):
    from taskconsole.workflows_n8n import execute_graph
    admitted=admit(svc,nodes,edges,'F-BRANCH held '+held)
    with ThreadPoolExecutor(1) as executor:
        future=executor.submit(execute_graph,svc,admitted['id'])
        try:
            deadline=time.monotonic()+60
            while time.monotonic()<deadline:
                current=svc.get_run(admitted['id'])
                if current['nodes'][held]['status']=='running' and current['nodes'][finished]['status'] in {'succeeded','failed'}:break
                time.sleep(.05)
            else:raise AssertionError(('Branches did not reach held/terminal barrier',current))
            # Independent file proves real held business worker started, while J cannot.
            root=svc.store.path/'workflow-runs'/admitted['id']
            marker=root/held/'attempt-1'/'project'/'business-calls'
            while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
            assert marker.read_text().splitlines()==['call']
            assert current['nodes']['J']['status']=='queued' and current['nodes']['J']['attempts']==[]
            assert not (root/'J'/'attempt-1'/'project'/'business-calls').exists()
            early=svc.acceptance_client.post(f'/internal/workflows/{admitted["id"]}/nodes/J',headers={'x-workflow-token':admitted['callback_token']})
            assert early.status_code==409 and early.json()['detail']['code']=='dependencies_pending',early.text
        finally:gate.touch()
        future.result(timeout=60)
    result=svc.get_run(admitted['id'])
    assert result.get('n8n_execution_id') and result.get('graph_execution_id'),result
    after_both(result,'J',['B','C'])
    assert_result(result)


@requires_n8n
@pytest.mark.parametrize('held',['B','C'])
def test_actual_n8n_append_uses_declared_order_under_reversed_completions(live,held):
    gate=live.store.path/'release-append'
    nodes,edges=fbranch('both')
    nodes[1 if held=='B' else 2]=native(held,B if held=='B' else C,gate=gate)
    nodes[3]=native('J',inputs={'B':mapping('B',path=['values']),'C':mapping('C',path=['values'])},config={'join':'all','merge':'append','merge_target':'items'},body='return inputs')
    def check(result):
        assert result['status']=='succeeded',result.get('error')
        assert result['nodes']['C' if held=='B' else 'B']['finished_at']<result['nodes'][held]['finished_at']
        assert result['nodes']['J']['inputs']=={'items':[1,2,3]}
        assert result['nodes']['J']['output']['data']=={'items':[1,2,3]}
        for nid in ['A','B','C','J']:calls(live,result,nid,1)
    gated_execution(live,nodes,edges,gate,held,'C' if held=='B' else 'B',check)


@requires_n8n
@pytest.mark.parametrize('required',[True,False])
def test_actual_n8n_any_join_waits_and_never_bypasses_required_failure(live,required):
    gate=live.store.path/'release-any'
    nodes,edges=fbranch('required_failure')
    nodes[2]=native('C',C,gate=gate)
    nodes[3]=native('J',inputs={'B':mapping('B',[]),'C':mapping('C')},config={'join':'any','merge':'named'},body='return inputs')
    edges[2]['required']=required
    def check(result):
        assert result['status']==('failed' if required else 'partial')
        assert result['nodes']['B']['status']=='failed' and result['nodes']['C']['status']=='succeeded'
        for nid in ['A','B','C']:calls(live,result,nid,1)
        calls(live,result,'J',0 if required else 1)
        if required:assert result['nodes']['J']['status']=='not_run' and result['nodes']['J']['reason']=='blocked_by_failed_dependency'
        else:
            assert result['nodes']['J']['status']=='succeeded'
            assert result['nodes']['J']['inputs']=={'B':[],'C':C}
            assert result['nodes']['J']['output']['data']=={'B':[],'C':C}
    gated_execution(live,nodes,edges,gate,'C','B',check)


@requires_n8n
def test_actual_n8n_four_nested_diamond_selections_finish_once(live):
    nodes=[];edges=[];cases=[]
    for first in [True,False]:
        for second in [True,False]:
            prefix=('t' if first else 'f')+('t' if second else 'f')+'_'
            ids={key:prefix+key for key in 'ABCJDEK'};cases.append((ids,first,second))
            nodes.extend([native(ids['A'],{'flag':first,'amount':7}),native(ids['B'],B),native(ids['C'],C),merged(ids['J'],ids['B'],ids['C'],flag=second),native(ids['D'],{'label':'D','values':[4]}),native(ids['E'],{'label':'E','values':[5]}),merged(ids['K'],ids['D'],ids['E'])])
            edges.extend([{'source':ids['A'],'target':ids[key],'condition':{'path':['flag'],'operator':'eq','value':flag}} for key,flag in [('B',True),('C',False)]])
            edges.extend([{'source':ids[key],'target':ids['J']} for key in ['B','C']])
            edges.extend([{'source':ids['J'],'target':ids[key],'condition':{'path':['flag'],'operator':'eq','value':flag}} for key,flag in [('D',True),('E',False)]])
            edges.extend([{'source':ids[key],'target':ids['K']} for key in ['D','E']])
    result=run_graph(live,nodes,edges,'All four nested diamonds')
    assert result['status']=='succeeded',(result.get('error'),result.get('adapter_log'))
    for ids,first,second in cases:
        selected={'A','J','K','B' if first else 'C','D' if second else 'E'}
        for key,nid in ids.items():
            assert result['nodes'][nid]['status']==('succeeded' if key in selected else 'skipped')
            calls(live,result,nid,1 if key in selected else 0)
        after_both(result,ids['J'],[ids['B'],ids['C']]);after_both(result,ids['K'],[ids['D'],ids['E']])
        assert result['nodes'][ids['J']]['output']['data']=={'sources':{'B':B} if first else {'C':C},'markers':{'B':'succeeded' if first else 'skipped','C':'skipped' if first else 'succeeded'},'flag':second}
        assert result['nodes'][ids['K']]['output']['data']=={'sources':{'B':{'label':'D','values':[4]}} if second else {'C':{'label':'E','values':[5]}},'markers':{'B':'succeeded' if second else 'skipped','C':'skipped' if second else 'succeeded'}}


@requires_n8n
def test_actual_n8n_disconnected_roots_wait_and_preserve_named_outputs(live):
    a={'flag':True,'amount':7}
    nodes=[native('A',a),native('B',B),native('J',inputs={'A':mapping('A'),'B':mapping('B')},config={'join':'all','merge':'named'},body='return inputs')]
    edges=[{'source':'A','target':'J'},{'source':'B','target':'J'}]
    result=run_graph(live,nodes,edges,'Disconnected root join')
    assert result['status']=='succeeded'
    assert result['nodes']['A']['inputs']==result['nodes']['B']['inputs']=={}
    assert result['nodes']['J']['inputs']=={'A':a,'B':B}
    assert result['nodes']['J']['output']['data']=={'A':a,'B':B}
    after_both(result,'J',['A','B'])
    for nid in ['A','B','J']:calls(live,result,nid,1)


def test_authenticated_publication_requires_explicit_join(live):
    client=live.acceptance_client
    saved=client.post('/api/workflows',json={'name':'Missing join policy','nodes':[native('A'),native('B'),native('J')],'edges':[{'source':'A','target':'J'},{'source':'B','target':'J'}]})
    assert saved.status_code==200,saved.text
    errors=saved.json()['validation_errors']
    assert any(e.get('node_id')=='J' and 'explicit join' in e['message'] for e in errors)
    response=client.post('/api/workflows/'+saved.json()['id']+'/publish')
    assert response.status_code==422 and response.json()['detail']['node_id']=='J'
    assert 'explicit join' in response.json()['detail']['message']
    assert client.get('/api/workflow-runs').json()==[]


def test_authenticated_unknown_condition_operator_rejects_publication(live):
    client=live.acceptance_client
    saved=client.post('/api/workflows',json={'name':'Unknown predicate','nodes':[native('A',{'x':7}),native('B')],'edges':[{'source':'A','target':'B','condition':{'path':['x'],'operator':'unknown','value':7}}]})
    assert saved.status_code==200
    assert any(e['node_id']=='A' and 'condition operator' in e['message'].lower() for e in saved.json()['validation_errors'])
    rejected=client.post('/api/workflows/'+saved.json()['id']+'/publish')
    assert rejected.status_code==422 and rejected.json()['detail']['node_id']=='A'
    assert isinstance(rejected.json()['detail']['code'],str)
    assert 'condition operator' in rejected.json()['detail']['message'].lower()
    assert client.get('/api/workflow-runs').json()==[]
