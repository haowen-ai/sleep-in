"""Published, admitted and test snapshots stay distinct through actual n8n."""
import hashlib
import json
from test_acceptance_data import live, configured_node
from test_acceptance_branches import native, calls, requires_n8n
from taskconsole.workflows_n8n import execute_graph
from taskconsole.workflows_operations import WorkflowOperations


@requires_n8n
def test_actual_n8n_publication_and_test_snapshots_remain_immutable(live):
    svc=live;client=svc.acceptance_client
    # Queue notification records only; no notification worker or external delivery.
    operations=WorkflowOperations(svc.store)
    channel=operations.save_channel({'name':'Version fixture','kind':'webhook','config':{'url':'http://127.0.0.1:9/never-sent'}})
    data={'name':'Version history','nodes':[native('A',{'marker':3})],'edges':[],
          'notifications':{'channel_ids':[channel['id']],'events':['succeeded']}}
    response=client.post('/api/workflows',json=data);assert response.status_code==200,response.text
    wf=response.json();prefix='/api/workflows/'+wf['id']
    first=client.post(prefix+'/publish');assert first.status_code==200,first.text
    first=first.json()
    with svc.store.transaction() as tx:v1=tx.get('workflow_version',first['version_id'])
    wf['nodes']=[native('A',{'marker':9})]
    assert client.put(prefix,json=wf).status_code==200
    old=client.post(prefix+'/run',json={'idempotency_key':'admit-v1-before-v2'});assert old.status_code==202
    old=old.json();assert old['version_id']==first['version_id']
    second=client.post(prefix+'/publish');assert second.status_code==200,second.text
    second=second.json();assert second['version']==2 and second['version_id']!=first['version_id']
    with svc.store.transaction() as tx:v2=tx.get('workflow_version',second['version_id'])
    execute_graph(svc,old['id']);old_result=svc.get_run(old['id'])
    assert old_result['status']=='succeeded' and old_result['nodes']['A']['output']['data']=={'marker':3}
    fresh=client.post(prefix+'/run',json={'idempotency_key':'admit-default-v2'}).json()
    assert fresh['version_id']==second['version_id'];execute_graph(svc,fresh['id'])
    fresh_result=svc.get_run(fresh['id'])
    assert fresh_result['status']=='succeeded' and fresh_result['nodes']['A']['output']['data']=={'marker':9}
    wf=client.get(prefix).json();wf['nodes']=[native('A',{'marker':4})]
    assert client.put(prefix,json=wf).status_code==200
    test=client.post(prefix+'/run',json={'test':True,'idempotency_key':'draft-test-4'});assert test.status_code==202,test.text
    test=test.json();assert test['test'] is True
    wf['nodes']=[native('A',{'marker':99})];assert client.put(prefix,json=wf).status_code==200
    execute_graph(svc,test['id']);test_result=svc.get_run(test['id'])
    assert test_result['status']=='succeeded' and test_result['nodes']['A']['output']['data']=={'marker':4}
    assert client.get(prefix).json()['published_version_id']==second['version_id']
    assert svc.get_run(old['id'])==old_result and svc.get_run(fresh['id'])==fresh_result
    with svc.store.transaction() as tx:
        assert tx.get('workflow_version',v1['id'])==v1 and tx.get('workflow_version',v2['id'])==v2
        assert len(tx.all('workflow_version'))==2
        notices=tx.all('workflow_notification')
    assert {n['run_id'] for n in notices}=={old['id'],fresh['id']}
    assert all(n['status']=='pending' for n in notices)
    for version in (v1,v2):
        assert hashlib.sha256(json.dumps(version['snapshot'],sort_keys=True).encode()).hexdigest()==version['digest']
    history=client.get('/api/workflow-runs',params={'workflow_id':wf['id']}).json()
    assert {r['id'] for r in history}=={old['id'],fresh['id'],test['id']}
    for run in (old_result,fresh_result,test_result):
        assert run['n8n_execution_id'] and run['graph_execution_id'];calls(svc,run,'A',1)
