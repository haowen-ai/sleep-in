"""Authenticated fresh-install transfer and unavailable-runtime authorization."""
import copy
import json

from fastapi.testclient import TestClient
from taskconsole.app import create_app
from test_workflow_api_contract import client, admin, graph
from test_workflow_transfer import authored_graph


def test_authenticated_portable_transfer_to_fresh_install_requires_rebinding(client, tmp_path, monkeypatch):
    admin(client)
    connection = client.post('/api/connections', json={'name': 'Private source', 'dialect': 'postgresql',
        'config': {'host': 'private-transfer.invalid', 'password': 'private-transfer-secret'}}).json()
    credential = client.post('/api/workflow-credentials', json={'name': 'Private key',
        'value': 'private-credential-canary', 'allowed_workflows': []}).json()
    profile = client.post('/api/runtime-profiles', json={'name': 'Source environment', 'language': 'python', 'config': {}}).json()
    data = authored_graph()
    data['nodes'][0]['config']['connection_id'] = connection['id']
    data['nodes'][1]['config'] = {'runtime_version_id': profile['versions'][0]['id']}
    data['nodes'][1]['inputs']['private'] = {'source': 'credential', 'credential_id': credential['id']}
    response = client.post('/api/workflows', json=data)
    assert response.status_code == 200, response.text
    original = response.json()
    response = client.get('/api/workflows/' + original['id'] + '/export')
    assert response.status_code == 200, response.text
    portable = response.json()
    encoded = json.dumps(portable)
    for excluded in ['private-transfer.invalid', 'private-transfer-secret', 'private-credential-canary',
                     connection['id'], credential['id'], profile['versions'][0]['id']]:
        assert excluded not in encoded
    assert portable['requirements']['runtimes'] == [{'language': 'python', 'node_ids': ['script']}]
    assert portable['requirements']['connections'][0]['dialect'] == 'postgresql'
    destination = tmp_path / 'fresh-install'
    monkeypatch.delenv('SLEEP_IN_LOCAL', raising=False)
    app = create_app(destination, 'sqlite:///' + str(destination / 'fresh.sqlite'))
    with TestClient(app) as other:
        admin(other)
        assert other.get('/api/workflows').json() == []
        response = other.post('/api/workflow-import', json={'template': portable})
        assert response.status_code == 200, response.text
        imported = response.json()
        assert imported['id'] != original['id']
        assert imported['requires_rebinding'] is True
        assert imported['enabled'] is False and imported.get('triggers', []) == []
        assert imported['schedule'] == {'kind': 'manual'} and imported['published_version_id'] is None
        assert imported['edges'] == original['edges'] and imported['params'] == original['params']
        for before, after in zip(original['nodes'], imported['nodes']):
            for key in ['id', 'name', 'kind', 'source', 'outputs', 'position']:
                assert after.get(key) == before.get(key)
            assert 'connection_id' not in after['config'] and 'runtime_version_id' not in after['config']
        expected_inputs = copy.deepcopy(original['nodes'][1]['inputs'])
        expected_inputs['private'] = {'source': 'credential'}
        assert imported['nodes'][1]['inputs'] == expected_inputs
        assert imported['validation_errors']
        assert other.post('/api/workflows/' + imported['id'] + '/publish').status_code == 422
        assert other.post('/api/workflows/' + imported['id'] + '/run', json={}).status_code == 422
        assert other.get('/api/workflow-runs').json() == []
        assert other.get('/api/connections').json() == []
        assert other.get('/api/runtime-profiles').json() == []
        assert other.get('/api/workflow-credentials').json() == []
    assert client.get('/api/workflows/' + original['id']).json() == original


def test_operator_cannot_override_unavailable_runtime_or_build_it(client):
    admin(client)
    profile = client.post('/api/runtime-profiles', json={'name': 'Not built', 'language': 'python', 'config': {}}).json()
    version = profile['versions'][0]
    assert version['status'] == 'draft' and version['verification']['status'] == 'not_run'
    data = graph()
    data['nodes'][0]['config']['runtime_version_id'] = version['id']
    response = client.post('/api/workflows', json=data)
    assert response.status_code == 200, response.text
    workflow = response.json()
    url = '/api/workflows/' + workflow['id']
    rejected = client.post(url + '/publish')
    assert rejected.status_code == 422
    before = client.get(url).json()
    with client.app.state.store.transaction() as tx:
        stored_before = copy.deepcopy(tx.get('workflow', workflow['id']))
    created = client.post('/api/admin/users', json={'username': 'limited', 'password': 'limited test account', 'role': 'operator'})
    assert created.status_code == 200, created.text
    client.post('/api/logout')
    login = client.post('/api/login', json={'username': 'limited', 'password': 'limited test account'})
    client.headers['X-CSRF-Token'] = login.json()['csrf']
    override = copy.deepcopy(workflow)
    override['nodes'][0]['config'].pop('runtime_version_id')
    for method, endpoint, payload in [
        ('put', url, override), ('post', url + '/publish', {}),
        ('post', '/api/runtime-profiles/' + profile['id'] + '/build', {'version_id': version['id']}),
        ('post', '/api/runtime-profiles/' + profile['id'] + '/versions', {'config': {}}),
        ('post', '/api/runtime-profiles', {'name': 'Override', 'language': 'python', 'config': {}}),
    ]:
        response = getattr(client, method)(endpoint, json=payload)
        assert response.status_code == 403, (endpoint, response.text)
    public_before = copy.deepcopy(before)
    for node in public_before['nodes']:
        node.pop('source', None)
    assert client.get(url).json() == public_before
    with client.app.state.store.transaction() as tx:
        assert tx.get('workflow', workflow['id']) == stored_before
        assert len(tx.all('runtime_version')) == 1
        assert tx.get('runtime_version', version['id'])['status'] == 'draft'
        assert tx.all('workflow_version') == [] and tx.all('workflow_run') == []
