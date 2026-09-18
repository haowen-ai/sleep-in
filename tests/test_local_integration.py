"""HTTP boundaries for the intentionally public fresh-local credentials."""
import pytest
from fastapi.testclient import TestClient
from taskconsole.app import create_app


def local_app(tmp_path, monkeypatch):
    monkeypatch.setenv('SLEEP_IN_LOCAL', '1')
    monkeypatch.setenv('APP_HOST', '127.0.0.1')
    return create_app(tmp_path, 'sqlite:///'+str(tmp_path/'console.db'))


def test_local_first_login_changes_hide_hint_and_revoke_session(tmp_path, monkeypatch):
    app = local_app(tmp_path, monkeypatch)
    with TestClient(app, base_url='http://127.0.0.1', client=('127.0.0.1', 50000)) as c:
        boot = c.get('/api/bootstrap').json()
        assert boot['initialized'] is True
        creds = boot['local_account']
        assert creds['username'] == 'admin'
        r = c.post('/api/login', json=creds)
        assert r.status_code == 200
        c.headers['X-CSRF-Token'] = r.json()['csrf']
        assert c.post('/api/me/password', json={'current_password':creds['password'], 'password':'user-changed-test-pass'}).status_code == 200
        assert c.get('/api/bootstrap').json()['local_account'] is None
        assert c.get('/api/tasks').status_code == 401


@pytest.mark.parametrize('headers', [
    {'host':'attacker.example'}, {'X-Forwarded-Host':'localhost'},
    {'Forwarded':'for=192.0.2.3'}, {'Origin':'https://attacker.example'},
])
def test_local_bootstrap_rejects_rebinding_and_proxy(tmp_path, monkeypatch, headers):
    app=local_app(tmp_path,monkeypatch)
    with TestClient(app,base_url='http://127.0.0.1',client=('127.0.0.1',50000)) as c:
        assert c.get('/api/bootstrap',headers=headers).status_code==403


def test_local_bootstrap_rejects_nonloopback_peer(tmp_path,monkeypatch):
    app=local_app(tmp_path,monkeypatch)
    with TestClient(app,base_url='http://localhost',client=('192.0.2.3',50000)) as c:
        assert c.get('/api/bootstrap').status_code==403


def test_local_mode_refuses_external_bind(tmp_path,monkeypatch):
    monkeypatch.setenv('SLEEP_IN_LOCAL','1');monkeypatch.setenv('APP_HOST','0.0.0.0')
    with pytest.raises(ValueError,match='loopback|local'):
        create_app(tmp_path,'sqlite:///'+str(tmp_path/'console.db'))


def test_public_initial_account_cannot_reopen_in_server_mode(tmp_path,monkeypatch):
    local_app(tmp_path,monkeypatch)
    monkeypatch.delenv('SLEEP_IN_LOCAL')
    monkeypatch.setenv('APP_HOST','0.0.0.0')
    with pytest.raises(ValueError,match='password|default'):
        create_app(tmp_path,'sqlite:///'+str(tmp_path/'console.db'))
def test_health_reports_only_its_startup_generation(tmp_path,monkeypatch):
    monkeypatch.delenv('SLEEP_IN_LOCAL',raising=False)
    monkeypatch.setenv('SLEEP_IN_INSTANCE_ID','synthetic-generation-one')
    app=create_app(tmp_path,'sqlite:///'+str(tmp_path/'identity.sqlite'))
    monkeypatch.setenv('SLEEP_IN_INSTANCE_ID','synthetic-generation-two')
    with TestClient(app) as client:
        assert client.get('/healthz').json()=={'status':'ok','instance_id':'synthetic-generation-one'}
