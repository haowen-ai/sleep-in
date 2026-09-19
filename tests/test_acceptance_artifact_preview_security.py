"""Actual v2 worker spill followed by authenticated malicious preview references."""
import copy
import hashlib
import json
from pathlib import Path
import pytest
from test_workflow_api_contract import client, admin


@pytest.mark.parametrize('escape', ['parent_traversal', 'outside_symlink', 'other_run'])
def test_sec009_structured_preview_rejects_host_and_cross_run_references_before_read(client, tmp_path, monkeypatch, escape):
    admin(client)
    svc = client.app.state.workflows
    graph = {'name': 'Actual structured producer', 'nodes': [{'id': 'source', 'kind': 'python',
        'source': 'def main(inputs): return {"rows":["safe-preview"],"large":"x"*1200000}',
        'inputs': {}, 'config': {}}], 'edges': []}
    wf = svc.save(graph)
    svc.publish(wf['id'])
    run = svc.admit(wf['id'])
    produced = svc.execute_node(run['id'], 'source')
    assert produced['status'] == 'succeeded' and produced['output']['data_ref']['size'] > 1024 * 1024
    svc.finish(run['id'])
    endpoint = '/api/workflow-runs/' + run['id'] + '/nodes/source/output?path=rows'
    assert client.get(endpoint).json()['data'] == ['safe-preview']
    root = svc.store.path / 'workflow-runs' / run['id']
    payload = json.dumps({'rows': ['HOST-PREVIEW-CANARY']}).encode()
    outside = svc.store.path / 'preview-host-canary.json'
    outside.write_bytes(payload)
    reference = {'size': len(payload), 'sha256': hashlib.sha256(payload).hexdigest()}
    if escape == 'parent_traversal':
        reference['path'] = str(root / '..' / '..' / outside.name)
    elif escape == 'outside_symlink':
        link = root / 'escaped-preview.json'
        link.symlink_to(outside)
        reference['path'] = str(link)
    else:
        other = svc.admit(wf['id'])
        assert svc.execute_node(other['id'], 'source')['status'] == 'succeeded'
        svc.finish(other['id'])
        reference = copy.deepcopy(svc.get_run(other['id'])['nodes']['source']['output']['data_ref'])
        outside = Path(reference['path'])
    before = outside.stat()
    with svc.store.transaction() as tx:
        stored = tx.get('workflow_run', run['id'])
        stored['nodes']['source']['output']['data_ref'] = reference
        tx.put('workflow_run', stored)
    opened = []
    original = Path.read_bytes
    def tracked(path):
        if path.resolve() == outside.resolve():
            opened.append(str(path))
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', tracked)
    response = client.get(endpoint)
    assert response.status_code == 400, response.text
    assert response.json()['detail']['code'] == 'invalid'
    assert 'outside' in response.text.lower() or 'missing' in response.text.lower()
    assert 'HOST-PREVIEW-CANARY' not in response.text and str(outside) not in response.text
    assert opened == []
    assert outside.stat().st_mtime_ns == before.st_mtime_ns and outside.stat().st_size == before.st_size
    public = client.get('/api/workflow-runs/' + run['id'])
    assert public.status_code == 200 and str(outside) not in public.text
