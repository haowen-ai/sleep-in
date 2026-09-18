"""Atomic retirement of uncertain manual admission keys; real auth/store/workers."""
from concurrent.futures import ThreadPoolExecutor
import copy
import threading
import pytest
from fastapi.testclient import TestClient
from test_workflow_api_contract import client,admin,graph
from taskconsole.workflows import WorkflowService


def save(client,publish=True):
    response=client.post('/api/workflows',json=graph());assert response.status_code==200,response.text
    wf=response.json()
    if publish:assert client.post('/api/workflows/'+wf['id']+'/publish').status_code==200
    return wf


def resolve(client,wid,key):
    return client.post('/api/workflows/'+wid+'/admission-resolution',json={'idempotency_key':key})


def test_retirement_is_durable_idempotent_and_blocks_late_manual_admission(client):
    admin(client);wf=save(client);key='uncertain-request'
    first=resolve(client,wf['id'],key)
    assert first.status_code==200,first.text
    assert first.json()=={'status':'retired','idempotency_key':key}
    store=client.app.state.store
    with store.transaction() as tx:records=copy.deepcopy(tx.all('workflow_admission_retirement'))
    assert len(records)==1
    assert resolve(client,wf['id'],key).json()==first.json()
    with store.transaction() as tx:assert tx.all('workflow_admission_retirement')==records
    # A fresh service instance observes persisted retirement, not an in-memory flag.
    svc=WorkflowService(store)
    with pytest.raises(ValueError,match='retired'):svc.admit(wf['id'],key=key)
    late=client.post('/api/workflows/'+wf['id']+'/run',json={'idempotency_key':key})
    assert late.status_code==422 and late.json()['detail']['code']=='admission_retired'
    assert client.get('/api/workflow-runs').json()==[]
    fresh=client.post('/api/workflows/'+wf['id']+'/run',json={'idempotency_key':'corrected-new-request'})
    assert fresh.status_code==202,fresh.text


def test_retirement_wins_race_with_an_already_preparing_draft_request(client,monkeypatch):
    admin(client);wf=save(client,publish=False);ready=threading.Event();release=threading.Event()
    import taskconsole.workflows_packs as packs
    original=packs.prepare_node
    def prepare(store,node):
        prepared=original(store,node)
        ready.set();assert release.wait(20)
        return prepared
    monkeypatch.setattr(packs,'prepare_node',prepare)
    with ThreadPoolExecutor(1) as pool:
        delayed=pool.submit(client.post,'/api/workflows/'+wf['id']+'/run',json={'test':True,'idempotency_key':'delayed-draft'})
        try:
            assert ready.wait(20)
            response=resolve(client,wf['id'],'delayed-draft')
            assert response.status_code==200 and response.json()['status']=='retired',response.text
        finally:release.set()
        late=delayed.result(timeout=20)
    assert late.status_code==422 and late.json()['detail']['code']=='admission_retired',late.text
    assert client.get('/api/workflow-runs').json()==[]


@pytest.mark.parametrize('complete',[False,True])
def test_late_resolution_returns_admitted_run_without_changing_it(client,complete):
    admin(client);wf=save(client);svc=WorkflowService(client.app.state.store)
    run=svc.admit(wf['id'],{'marker':'original'},key='committed-before-response-loss')
    if complete:svc.execute_node(run['id'],'source');svc.finish(run['id'])
    before=svc.get_run(run['id'])
    response=resolve(client,wf['id'],'committed-before-response-loss')
    assert response.status_code==200,response.text
    assert response.json()['status']=='admitted'
    public=response.json()['run']
    assert public['id']==run['id'] and public['status']==('succeeded' if complete else 'queued')
    assert public['params']=={'marker':'original'}
    assert 'callback_token' not in public and 'snapshot' not in public
    assert svc.get_run(run['id'])==before
    assert resolve(client,wf['id'],'committed-before-response-loss').json()==response.json()
    with svc.store.transaction() as tx:assert tx.all('workflow_admission_retirement')==[]


def test_resolution_keys_are_isolated_by_workflow_and_manual_trigger(client):
    admin(client);one=save(client);two=save(client);svc=WorkflowService(client.app.state.store)
    assert resolve(client,one['id'],'same-key').status_code==200
    sibling=svc.admit(two['id'],key='same-key')
    assert resolve(client,two['id'],'same-key').json()['run']['id']==sibling['id']
    triggered=svc.admit(one['id'],key='same-key',trigger_id='explicit-api-trigger')
    assert triggered['trigger_id']=='explicit-api-trigger'
    # An unrelated trigger cannot satisfy manual resolution of the same key.
    assert resolve(client,one['id'],'same-key').json()['status']=='retired'
    svc.cancel(triggered['id'])
    denied=client.post('/api/workflows/'+one['id']+'/run',json={'idempotency_key':'same-key'})
    assert denied.status_code==422 and denied.json()['detail']['code']=='admission_retired'


def test_resolution_auth_csrf_and_workflow_authorization_are_non_mutating(client):
    admin(client);wf=save(client);store=client.app.state.store
    with TestClient(client.app) as anonymous:
        assert resolve(anonymous,wf['id'],'private-key').status_code==401
    csrf=client.headers.pop('X-CSRF-Token')
    assert resolve(client,wf['id'],'private-key').status_code==403
    client.headers['X-CSRF-Token']=csrf
    owner=client.get('/api/bootstrap').json()['user']['id']
    wf['allowed_user_ids']=[owner]
    assert client.put('/api/workflows/'+wf['id'],json=wf).status_code==200
    assert client.post('/api/admin/users',json={'username':'operator','password':'operator recovery password','role':'operator'}).status_code==200
    assert client.post('/api/logout').status_code==200
    login=client.post('/api/login',json={'username':'operator','password':'operator recovery password'})
    client.headers['X-CSRF-Token']=login.json()['csrf']
    assert resolve(client,wf['id'],'private-key').status_code==403
    with store.transaction() as tx:
        assert tx.all('workflow_admission_retirement')==[]
        assert tx.all('workflow_run')==[]


@pytest.mark.parametrize('key',[None,'',False,17,{},[]])
def test_resolution_requires_an_explicit_nonempty_string_key(client,key):
    admin(client);wf=save(client,publish=False)
    response=resolve(client,wf['id'],key)
    assert response.status_code==422,response.text
    assert response.json()['detail']['field']=='idempotency_key'
    with client.app.state.store.transaction() as tx:assert tx.all('workflow_admission_retirement')==[]
