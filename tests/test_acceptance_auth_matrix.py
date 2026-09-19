"""Every public workflow mutation fails closed before touching persistent state."""
import copy
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text
from taskconsole.workflows import WorkflowService
from test_workflow_api_contract import client, admin, graph
from test_acceptance_lifecycle import local


@pytest.mark.parametrize('denial',['anonymous','missing_csrf','cross_origin'])
def test_all_workflow_mutations_and_artifact_reads_enforce_auth_without_state_change(client,denial):
    admin(client);store=client.app.state.store;svc=WorkflowService(store)
    data=graph();data['nodes'][0].update(config={'entry_mode':'file'},source='''import os,json
from pathlib import Path
Path(os.environ["SLEEP_IN_ARTIFACT_DIR"],"evidence.txt").write_text("authorized fixture bytes")
Path(os.environ["SLEEP_IN_OUTPUT_FILE"]).write_text(json.dumps({"schemaVersion":1,"data":{"value":7},"artifacts":[{"name":"evidence.txt","path":"evidence.txt","mediaType":"text/plain"}]}))
''')
    created=client.post('/api/workflows',json=data);assert created.status_code==200
    wf=created.json();prefix='/api/workflows/'+wf['id']
    assert client.post(prefix+'/publish').status_code==200
    admitted=client.post(prefix+'/run',json={'idempotency_key':'artifact-producer'});assert admitted.status_code==202
    rid=admitted.json()['id'];node=svc.execute_node(rid,'source')
    assert node['status']=='succeeded';svc.finish(rid)
    item=svc.get_run(rid)['artifacts'][0]
    download='/api/workflow-runs/'+rid+'/artifacts/'+item['id']
    good=client.get(download);assert good.status_code==200 and good.content==b'authorized fixture bytes'
    queued=client.post(prefix+'/run',json={'idempotency_key':'cancel-target'}).json()
    modified=copy.deepcopy(data);modified['name']='Unauthorized mutation'
    requests=[('POST','/api/workflows',data),('PUT',prefix,modified),
        ('POST',prefix+'/publish',{}),('POST',prefix+'/run',{'idempotency_key':'unauthorized-run'}),
        ('POST','/api/workflow-runs/'+queued['id']+'/cancel',{})]
    def snapshot():
        with store.engine.connect() as connection:
            return connection.execute(text('SELECT kind,id,payload FROM console_records ORDER BY kind,id')).all()
    before=snapshot()
    with TestClient(client.app) as attacker:
        if denial!='anonymous':attacker.cookies.update(client.cookies)
        if denial=='cross_origin':
            attacker.headers.update({'X-CSRF-Token':client.headers['X-CSRF-Token'],'Origin':'https://untrusted.invalid'})
        for method,path,body in requests:
            response=attacker.request(method,path,json=body)
            assert response.status_code==(401 if denial=='anonymous' else 403),(method,path,response.text)
            assert snapshot()==before,(method,path)
        if denial=='anonymous':
            response=attacker.get(download)
            assert response.status_code==401 and b'authorized fixture bytes' not in response.content
            assert snapshot()==before


@pytest.mark.parametrize('headers,peer',[
    ({'Host':'external.invalid:18765'},'127.0.0.1'),
    ({'Host':'127.0.0.1:18766'},'127.0.0.1'),
    ({'Forwarded':'for=127.0.0.1;host=127.0.0.1:18765'},'127.0.0.1'),
    ({'X-Forwarded-For':'127.0.0.1'},'127.0.0.1'),
    ({'Origin':'https://untrusted.invalid'},'127.0.0.1'),
    ({},'192.0.2.12'),
])
def test_internal_execution_routes_reject_forwarded_external_requests_before_effects(local,headers,peer):
    store=local.state.store;svc=WorkflowService(store)
    wf=svc.save(graph());svc.publish(wf['id']);run=svc.admit(wf['id'],key='protected-callback')
    base='/internal/workflows/'+run['id']
    capability={'x-workflow-token':run['callback_token'],'x-dispatch-token':(store.path/'dispatch-token').read_text().strip()}
    routes=[('POST',base+'/claim'),('POST',base+'/nodes/source/submit'),
            ('GET',base+'/nodes/source/status'),('POST',base+'/nodes/source'),
            ('POST',base+'/finish'),('POST','/internal/workflow-tick')]
    def snapshot():
        with store.engine.connect() as connection:
            return connection.execute(text('SELECT kind,id,payload FROM console_records ORDER BY kind,id')).all()
    before=snapshot()
    with TestClient(local,base_url='http://127.0.0.1:18765',client=(peer,50000)) as caller:
        for method,path in routes:
            response=caller.request(method,path,headers={**capability,**headers},**({'json':{}} if method=='POST' else {}))
            assert response.status_code==403,(path,response.text)
            assert response.json()['detail']['code']=='local_only'
            assert snapshot()==before
    with TestClient(local,base_url='http://127.0.0.1:18765',client=('127.0.0.1',50000)) as direct:
        response=direct.get(base+'/nodes/source/status',headers=capability)
        assert response.status_code==200 and response.json()['status']=='queued'
    assert not (store.path/'workflow-runs'/run['id']).exists()
