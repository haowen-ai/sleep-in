"""Public API boundaries: real HTTP/auth/store, no external database or scheduler."""
import pytest
from datetime import timedelta
from taskconsole.store import now, stamp
from fastapi.testclient import TestClient
from taskconsole.app import create_app


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.delenv('SLEEP_IN_LOCAL', raising=False)
    app = create_app(tmp_path, 'sqlite:///' + str(tmp_path / 'api.sqlite'))
    with TestClient(app) as client:
        yield client


def admin(client):
    token = (client.app.state.store.path / 'setup-token').read_text().strip()
    response = client.post('/api/setup', json={'token': token, 'username': 'owner',
        'password': 'workflow test password', 'timezone': 'UTC', 'locale': 'en'})
    assert response.status_code == 200, response.text
    client.headers['X-CSRF-Token'] = response.json()['csrf']


def graph():
    return {'name': 'HTTP contract', 'description': 'Synthetic example', 'nodes': [
        {'id': 'source', 'name': 'Source', 'kind': 'python',
         'source': 'def main(inputs): return {"value": 7}', 'config': {}, 'inputs': {},
         'position': {'x': 80, 'y': 100}}], 'edges': [], 'params': {},
        'schedule': {'kind': 'manual'}, 'timezone': 'UTC', 'enabled': False}


def saved(client):
    response = client.post('/api/workflows', json=graph())
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('path', ['/api/workflows', '/api/workflow-runs',
    '/api/workflow-templates', '/api/connections', '/api/runtimes'])
def test_workflow_catalogs_require_login(client, path):
    response = client.get(path)
    assert response.status_code == 401, response.text
    assert response.json()['detail']['code'] == 'unauthorized'


def test_csrf_blocks_mutation_before_store_changes(client):
    admin(client)
    del client.headers['X-CSRF-Token']
    response = client.post('/api/workflows', json=graph())
    assert response.status_code == 403
    assert client.get('/api/workflows').json() == []


def test_draft_save_never_publishes_and_run_requires_publication(client):
    admin(client)
    workflow = saved(client)
    assert not workflow.get('published_version_id')
    response = client.post('/api/workflows/' + workflow['id'] + '/run', json={'params': {}})
    assert response.status_code in {400, 409, 422}
    assert isinstance(response.json()['detail']['code'], str)
    assert 'publish' in response.json()['detail']['message'].lower()
    assert client.get('/api/workflow-runs').json() == []


def test_published_run_dedupe_snapshot_and_cancel(client):
    admin(client)
    workflow = saved(client)
    prefix = '/api/workflows/' + workflow['id']
    publication = client.post(prefix + '/publish')
    assert publication.status_code == 200, publication.text
    body = {'params': {'message': 'original'}, 'idempotency_key': 'http-same-click'}
    first = client.post(prefix + '/run', json=body)
    assert first.status_code == 202, first.text
    run = first.json()
    again = client.post(prefix + '/run', json=body)
    assert again.json()['id'] == run['id']
    workflow['nodes'][0]['source'] = 'def main(inputs): return {"value": 999}'
    assert client.put(prefix, json=workflow).status_code == 200
    detail = client.get('/api/workflow-runs/' + run['id']).json()
    assert detail['params'] == {'message': 'original'}
    assert detail['version_id'] == publication.json()['version_id']
    with client.app.state.store.transaction() as tx:
        persisted = tx.get('workflow_run', run['id'])
    assert '999' not in persisted['snapshot']['nodes'][0]['source']
    cancelled = client.post('/api/workflow-runs/' + run['id'] + '/cancel')
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()['status'] == 'cancelled'


def test_operator_cannot_author_publish_test_or_read_credentials(client):
    admin(client)
    workflow = saved(client)
    prefix = '/api/workflows/' + workflow['id']
    assert client.post(prefix + '/publish').status_code == 200
    assert client.post('/api/admin/users', json={'username': 'operator',
        'password': 'operator test password', 'role': 'operator'}).status_code == 200
    client.post('/api/logout')
    login = client.post('/api/login', json={'username': 'operator', 'password': 'operator test password'})
    client.headers['X-CSRF-Token'] = login.json()['csrf']
    assert client.get('/api/workflows').status_code == 200
    for path, method, body in [('/api/workflows', 'post', graph()),
        (prefix, 'put', graph()), (prefix + '/publish', 'post', {}),
        (prefix + '/run', 'post', {'test': True}),
        ('/api/connections', 'post', {'name': 'No access', 'dialect': 'sqlite', 'config': {'synthetic': True}})]:
        response = getattr(client, method)(path, json=body)
        assert response.status_code == 403, (path, response.text)
    response = client.post(prefix + '/run', json={'params': {}})
    assert response.status_code == 202, response.text


def test_connection_secret_not_returned_in_metadata(client):
    admin(client)
    secret = 'synthetic-credential-not-real'
    response = client.post('/api/connections', json={'name': 'Offline metadata',
        'dialect': 'postgresql', 'config': {'host': '127.0.0.1', 'port': 65432,
        'database': 'test', 'user': 'test', 'password': secret}, 'write_enabled': False})
    assert response.status_code == 200, response.text
    assert secret not in response.text
    assert secret not in client.get('/api/connections').text


def test_preview_returns_structured_errors_and_fixed_oracle(client):
    admin(client)
    workflow = saved(client)
    prefix = '/api/workflows/' + workflow['id'] + '/preview'
    valid = client.post(prefix, json={'schedule': {'kind': 'weekdays', 'time': '07:00'},
        'timezone': 'UTC', 'after': '2026-09-25T08:00:00Z'})
    assert valid.status_code == 200, valid.text
    assert valid.json()['next_runs'][0].startswith('2026-09-28T07:00:00')
    for payload in [{'schedule': {'kind': 'cron', 'cron': '* * * * *'}},
                    {'schedule': {'kind': 'daily', 'time': '25:00'}},
                    {'schedule': {'kind': 'daily', 'time': '07:00'}, 'timezone': 'Moon/Sea'}]:
        invalid = client.post(prefix, json=payload)
        assert invalid.status_code in {400, 422}, invalid.text
        assert isinstance(invalid.json()['detail']['message'], str)


def test_workflow_health_uses_fresh_worker_not_legacy_tick(client):
    assert client.get('/api/bootstrap').json()['workflow_scheduler']['status'] == 'unavailable'
    with client.app.state.store.transaction() as tx:
        tx.put('meta', {'id': 'workflow_worker', 'status': 'ready', 'n8n_available': True,
            'last_seen': stamp(), 'pid': 1234})
    result = client.get('/api/bootstrap').json()
    assert result['workflow_scheduler']['status'] == 'ready'
    assert result['scheduler']['status'] == 'unavailable'
    assert 'pid' not in result['workflow_scheduler']
    with client.app.state.store.transaction() as tx:
        worker = tx.get('meta', 'workflow_worker')
        worker['last_seen'] = (now() - timedelta(seconds=60)).isoformat()
        tx.put('meta', worker)
    assert client.get('/api/bootstrap').json()['workflow_scheduler']['status'] == 'unavailable'
