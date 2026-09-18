"""Connection rotation is metadata-only; live execution uses synthetic SQLite."""
import json
import pytest
from test_workflow_api_contract import client,admin
from test_workflows import service


def test_rotation_http_admin_csrf_and_no_secret_response(client):
    admin(client)
    response=client.post('/api/connections',json={'name':'Synthetic','dialect':'postgresql','config':{'host':'localhost','password':'old-secret','sslmode':'require'}})
    assert response.status_code==200;connection=response.json();cid=connection['id']
    changed=client.put('/api/connections/'+cid,json={'config':{'host':'127.0.0.1','password':'new-secret'},'allowed_workflows':['workflow-only']})
    assert changed.status_code==200,changed.text
    assert changed.json()['revision']!=connection['revision'] and changed.json()['status']=='untested'
    assert 'new-secret' not in changed.text and 'old-secret' not in client.get('/api/connections').text
    assert changed.json()['allowed_workflows']==['workflow-only']
    token=client.headers.pop('X-CSRF-Token')
    assert client.put('/api/connections/'+cid,json={'name':'Denied'}).status_code==403
    client.headers['X-CSRF-Token']=token
    assert client.post('/api/admin/users',json={'username':'reader','password':'reader-test-password','role':'operator'}).status_code==200
    client.post('/api/logout');login=client.post('/api/login',json={'username':'reader','password':'reader-test-password'});client.headers['X-CSRF-Token']=login.json()['csrf']
    assert client.put('/api/connections/'+cid,json={'name':'Denied'}).status_code==403


def test_rotation_preserves_omitted_blank_and_nested_credentials(tmp_path):
    from taskconsole import workflows_sql as sql
    svc=service(tmp_path);old=sql.create_connection(svc.store,{'name':'Metadata','dialect':'mysql','config':{'host':'localhost','password':'keep-secret','ssl':{'ca':'root.pem','cert':'client.pem'}}})
    assert hasattr(sql,'update_connection'),'connection rotation missing'
    new=sql.update_connection(svc.store,old['id'],{'config':{'password':'','ssl':{'ca':'next.pem'}},'write_enabled':True})
    with svc.store.transaction() as tx:stored=tx.get('connection',old['id'])
    config=json.loads(svc.store.fernet.decrypt(stored['encrypted_config'].encode()))
    assert config=={'host':'localhost','password':'keep-secret','ssl':{'ca':'next.pem','cert':'client.pem'}}
    assert new['write_enabled'] and new['revision']!=old['revision']
    with pytest.raises(ValueError,match='dialect'):sql.update_connection(svc.store,old['id'],{'dialect':'postgresql'})
    with pytest.raises(ValueError):sql.update_connection(svc.store,old['id'],{'allowed_workflows':'not-an-array'})


def test_published_run_uses_current_connection_revision_and_authorization(tmp_path):
    from taskconsole import workflows_sql as sql
    svc=service(tmp_path);graph=svc.templates()[0];graph['nodes']=graph['nodes'][:1];graph['edges']=[]
    cid=graph['nodes'][0]['config']['connection_id'];wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id'])
    assert hasattr(sql,'update_connection'),'connection rotation missing'
    rotated=sql.update_connection(svc.store,cid,{'name':'Rotated current connection','allowed_workflows':[wf['id']]})
    svc.execute_node(run['id'],'orders');result=svc.finish(run['id'])
    assert result['status']=='succeeded' and result['nodes']['orders']['credential_revision']==rotated['revision']
    assert len(result['nodes']['orders']['output']['data']['rows'])==3
    blocked=svc.admit(wf['id']);sql.update_connection(svc.store,cid,{'allowed_workflows':['different-workflow']})
    svc.execute_node(blocked['id'],'orders')
    assert svc.finish(blocked['id'])['status']=='failed'
