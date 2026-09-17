from fastapi.testclient import TestClient
from pathlib import Path
import pytest
from taskconsole.app import create_app

@pytest.fixture
def client(tmp_path):
    app=create_app(state_dir=tmp_path,database_url='sqlite:///'+str(tmp_path/'test.db'))
    with TestClient(app) as c:
        yield c

def setup(c,username='owner'):
    token=(c.app.state.store.path/'setup-token').read_text().strip()
    r=c.post('/api/setup',json={'token':token,'username':username,'password':'test password 12345','timezone':'UTC','locale':'en'})
    assert r.status_code==200,r.text
    c.headers['X-CSRF-Token']=r.json()['csrf']
    return r.json()

def task(c,**kw):
    scripts=c.get('/api/scripts').json()
    data={'name':'Example','version_id':scripts[0]['default_version'],'params':{},'recipients':[],'schedule':{'kind':'manual'},'timezone':'UTC','timeout':10,'enabled':False}
    data.update(kw)
    r=c.post('/api/tasks',json=data)
    assert r.status_code==200,r.text
    return r.json()

def test_setup_single_use_auth_csrf_locale(client):
    c=client
    assert c.get('/api/tasks').status_code==401
    assert c.get('/api/bootstrap').json()['user'] is None
    setup(c)
    assert c.post('/api/setup',json={}).status_code==409
    assert c.patch('/api/me',json={'locale':'zh-CN'}).json()['locale']=='zh-CN'
    del c.headers['X-CSRF-Token']
    assert c.post('/api/tasks',json={}).status_code==403
    assert c.get('/api/bootstrap').json()['user']['locale']=='zh-CN'

def test_run_dedup_overlap_snapshot_cancel(client):
    c=client;setup(c); t=task(c,params={'text':'你好\noriginal'})
    r=c.post(f"/api/tasks/{t['id']}/run",headers={'Idempotency-Key':'one'}).json()
    assert r['status']=='queued'
    assert c.post(f"/api/tasks/{t['id']}/run",headers={'Idempotency-Key':'one'}).json()['id']==r['id']
    assert c.post(f"/api/tasks/{t['id']}/run",headers={'Idempotency-Key':'two'}).json()['status']=='skipped'
    t['params']={'text':'changed'}
    assert c.put(f"/api/tasks/{t['id']}",json=t).status_code==200
    assert c.get(f"/api/executions/{r['id']}").json()['params']['text']=='你好\noriginal'
    assert c.post(f"/api/executions/{r['id']}/cancel").json()['status']=='cancelled'

def test_operator_cannot_manage_scripts_or_secrets(client):
    c=client;setup(c)
    assert c.post('/api/admin/users',json={'username':'operator','password':'operator pass 123','role':'operator'}).status_code==200
    c.post('/api/logout')
    r=c.post('/api/login',json={'username':'operator','password':'operator pass 123'})
    c.headers['X-CSRF-Token']=r.json()['csrf']
    assert c.get('/api/scripts').status_code==200
    assert c.post('/api/scripts',json={'name':'x','source':'print(1)'}).status_code==403
    assert c.get('/api/admin/variables').status_code==403
    assert c.get('/api/admin/users').status_code==403

def test_secret_never_returned_last_admin_protected(client):
    c=client;u=setup(c)['user']
    assert c.post('/api/admin/variables',json={'name':'API_TOKEN','scope':'instance','value':'not-a-real-secret-value'}).status_code==200
    assert 'not-a-real-secret-value' not in c.get('/api/admin/variables').text
    assert c.patch('/api/admin/users/'+u['id'],json={'enabled':False}).status_code==409

def test_password_change_revokes_session(client):
    c=client;setup(c)
    assert c.post('/api/me/password',json={'current_password':'test password 12345','password':'changed password 456'}).status_code==200
    assert c.get('/api/tasks').status_code==401

def test_tick_requires_secret_and_dispatches_once(client):
    c=client;setup(c);t=task(c,schedule={'kind':'interval','every':1},enabled=True)
    assert c.post('/internal/tick').status_code==403
    with c.app.state.store.transaction() as tx:
        saved=tx.get('task',t['id']);saved['next_run']='2026-01-01T00:00:00+00:00';tx.put('task',saved)
    token=(c.app.state.store.path/'dispatch-token').read_text().strip()
    assert c.post('/internal/tick',headers={'X-Dispatch-Token':token}).status_code==200
    assert c.post('/internal/tick',headers={'X-Dispatch-Token':token}).status_code==200
    rows=c.get('/api/executions').json()['items']
    assert len(rows)==1
    assert rows[0]['status']=='skipped' # no replay after outage
