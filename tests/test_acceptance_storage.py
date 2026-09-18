"""Controlled SQLite persistence faults; no host disk or user state changes."""
from sqlalchemy import text
from test_workflow_api_contract import client, admin, saved


def test_failed_admission_is_actionable_atomic_and_retry_keeps_identity(client):
    admin(client)
    wf = saved(client)
    route = '/api/workflows/' + wf['id']
    assert client.post(route + '/publish', json={}).status_code == 200
    store = client.app.state.store
    with store.transaction() as tx:
        before = tx.all('workflow')
        settings = tx.all('meta')
        publications = tx.all('workflow_version')
    # A real SQLite trigger rejects the transaction at its durable write boundary.
    # The injected diagnostic contains a canary to prove no SQL/parameters escape.
    with store.engine.begin() as conn:
        conn.exec_driver_sql("CREATE TRIGGER acceptance_storage_fault BEFORE INSERT ON console_records "
                             "WHEN NEW.kind='workflow_run' BEGIN SELECT RAISE(ABORT, 'storage-secret-canary'); END")
    client._transport.raise_server_exceptions = False
    body = {'idempotency_key': 'storage-retry-once', 'params': {'label': 'preserved'}}
    failed = client.post(route + '/run', json=body)
    assert failed.status_code == 503, failed.text
    assert failed.json()['detail']['code'] == 'storage_unavailable'
    assert 'storage' in failed.json()['detail']['message'].lower()
    assert 'storage-secret-canary' not in failed.text
    assert 'INSERT' not in failed.text
    with store.transaction() as tx:
        assert tx.all('workflow_run') == []
        assert tx.all('workflow') == before
        assert tx.all('meta') == settings
        assert tx.all('workflow_version') == publications
    with store.engine.begin() as conn:
        conn.exec_driver_sql('DROP TRIGGER acceptance_storage_fault')
    first = client.post(route + '/run', json=body)
    second = client.post(route + '/run', json=body)
    assert first.status_code == second.status_code == 202
    assert first.json()['id'] == second.json()['id']
    assert first.json()['params']['label'] == 'preserved'
    assert len(client.get('/api/workflow-runs').json()) == 1
    with store.engine.connect() as conn:
        assert conn.execute(text('PRAGMA integrity_check')).scalar() == 'ok'
