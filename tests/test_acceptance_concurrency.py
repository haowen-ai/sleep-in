"""Authenticated contention and callback capability oracles, actual n8n."""
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import pytest
from test_acceptance_data import live


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'), reason='Actual n8n fixture required')
def test_twenty_admissions_twenty_callbacks_one_effect_and_safe_capabilities(live):
    from taskconsole.workflows_n8n import execute_graph
    svc = live
    client = svc.acceptance_client
    ledger = svc.store.path / 'independent-effects.txt'
    source = ('from pathlib import Path\n'
              'def main(inputs):\n'
              '    import time\n'
              '    time.sleep(.25)\n'
              f'    with Path({str(ledger)!r}).open("a") as f: f.write("effect-001\\n")\n'
              '    return {"effect": 1}\n')
    response = client.post('/api/workflows', json={'name': 'Twenty requests', 'nodes': [
        {'id': 'effect', 'kind': 'python', 'name': 'Effect', 'source': source, 'inputs': {}, 'config': {}}], 'edges': []})
    assert response.status_code == 200, response.text
    wf = response.json()
    assert client.post(f'/api/workflows/{wf["id"]}/publish', json={}).status_code == 200
    barrier = threading.Barrier(20)
    def admit(_):
        barrier.wait(timeout=15)
        return client.post(f'/api/workflows/{wf["id"]}/run', json={'idempotency_key': 'same-twenty'})
    with ThreadPoolExecutor(max_workers=20) as pool:
        responses = list(pool.map(admit, range(20)))
    assert {r.status_code for r in responses} == {202}, [r.text for r in responses]
    ids = {r.json()['id'] for r in responses}
    assert len(ids) == 1
    rid = ids.pop()
    assert len(client.get('/api/workflow-runs').json()) == 1
    run = svc.get_run(rid)
    capability = {'x-workflow-token': run['callback_token']}
    prefix = f'/internal/workflows/{rid}'

    # Wrong capabilities cannot mutate a queued node, finish early, or tick work.
    before = svc.get_run(rid)
    for endpoint in (prefix + '/nodes/effect/submit', prefix + '/finish', '/internal/workflow-tick'):
        for headers in ({}, {'x-workflow-token': 'wrong', 'x-dispatch-token': 'wrong'}):
            denied = client.post(endpoint, json={}, headers=headers)
            assert denied.status_code in {401, 403}
    assert svc.get_run(rid) == before
    assert not ledger.exists()
    early = client.post(prefix + '/finish', json={}, headers=capability)
    assert early.status_code >= 400 or early.json().get('status') != 'succeeded'
    assert svc.get_run(rid)['status'] != 'succeeded'
    assert client.post(prefix + '/nodes/not-in-snapshot', json={}, headers=capability).status_code >= 400
    assert not ledger.exists()

    barrier = threading.Barrier(20)
    def callback(_):
        barrier.wait(timeout=15)
        return client.post(prefix + '/nodes/effect', headers=capability,
                           json={'source': 'raise RuntimeError("injected")', 'run_id': 'another', 'version_id': 'another'})
    with ThreadPoolExecutor(max_workers=20) as pool:
        callbacks = list(pool.map(callback, range(20)))
    assert {r.status_code for r in callbacks} == {200}, [r.text for r in callbacks]
    assert all(r.json()['status'] == 'succeeded' for r in callbacks)
    assert ledger.read_text().splitlines() == ['effect-001']
    assert svc.get_run(rid)['nodes']['effect']['output']['data'] == {'effect': 1}

    execute_graph(svc, rid)
    terminal = svc.get_run(rid)
    assert terminal['status'] == 'succeeded'
    assert terminal.get('n8n_execution_id') and terminal.get('graph_execution_id')
    assert len(terminal['nodes']['effect']['attempts']) == 1
    for _ in range(2):
        assert client.post(prefix + '/finish', json={}, headers=capability).json()['status'] == 'succeeded'
    assert ledger.read_text().splitlines() == ['effect-001']
    assert svc.get_run(rid) == terminal
    with svc.store.transaction() as tx:
        assert tx.all('workflow_notification') == []
