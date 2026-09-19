"""Actual producer workers and authenticated artifact boundaries in disposable state."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import unquote

import httpx
import pytest
from test_acceptance_data import live, configured_node
from taskconsole.store import now
from taskconsole.workflows_runtime import run_script


def producer(nid='a', content='fixture-bytes', name='same.txt', extra=''):
    return {'id': nid, 'kind': 'python', 'config': {'entry_mode': 'file'}, 'inputs': {},
        'source': 'import os,json\nfrom pathlib import Path\nartifacts=Path(os.environ["SLEEP_IN_ARTIFACT_DIR"])\n'
        f'(artifacts/"payload.bin").write_bytes({content.encode()!r})\n' + extra +
        f'json.dump({{"schemaVersion":1,"data":{{}},"artifacts":[{{"name":{name!r},"path":"payload.bin","mediaType":"text/plain"}}]}},open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))\n'}


def admit(svc, nodes, edges=None, **settings):
    wf = svc.save({'name': 'Artifact acceptance', 'nodes': nodes, 'edges': edges or [], **settings})
    svc.publish(wf['id'])
    return wf, svc.admit(wf['id'])


def download(svc, run, artifact):
    return svc.acceptance_client.get(f'/api/workflow-runs/{run["id"]}/artifacts/{artifact["id"]}')


@pytest.mark.parametrize('escape', ['traversal', 'outside_symlink', 'other_node_symlink'])
def test_art04_05_06_real_producer_rejects_escape_without_reading_target(live, monkeypatch, escape):
    svc = live
    outside = svc.store.path / 'host-canary.txt'
    outside.write_bytes(b'PRIVATE-CANARY-ARTIFACT-MUST-NOT-BE-READ')
    node = producer('b')
    # File worker declares a path; it never reads the canary itself.
    prefix = 'import os,json\nfrom pathlib import Path\na=Path(os.environ["SLEEP_IN_ARTIFACT_DIR"])\n'
    if escape == 'traversal':
        prefix += 'declared="../../outside.txt"\n'
    elif escape == 'outside_symlink':
        prefix += f'(a/"escape").symlink_to({str(outside)!r})\ndeclared="escape"\n'
    else:
        prefix += '(a/"escape").symlink_to(a.parents[2]/"a"/"attempt-1"/"artifacts"/"payload.bin")\ndeclared="escape"\n'
    node['source'] = prefix + 'json.dump({"schemaVersion":1,"data":{},"artifacts":[{"name":"escape","path":declared}]},open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))'
    wf, run = admit(svc, [producer('a', outside.read_text()), node], [{'source': 'a', 'target': 'b'}])
    assert svc.execute_node(run['id'], 'a')['status'] == 'succeeded'
    if escape == 'traversal':
        target = svc.store.path / 'workflow-runs' / run['id'] / 'b' / 'outside.txt'
        target.parent.mkdir(parents=True); target.write_bytes(outside.read_bytes())
    elif escape == 'outside_symlink':
        target = outside
    else:
        target = Path(svc.get_run(run['id'])['artifacts'][0]['path'])
    before = target.stat()
    original = Path.read_bytes; opened = []
    def tracked(path):
        if path.resolve() == target.resolve():
            opened.append(str(path))
        return original(path)
    monkeypatch.setattr(Path, 'read_bytes', tracked)
    rejected = svc.execute_node(run['id'], 'b')
    assert rejected['status'] == 'failed' and 'node-local' in rejected['error']
    assert opened == []
    assert target.stat().st_mtime_ns == before.st_mtime_ns
    finished = svc.finish(run['id'])
    assert finished['status'] == 'failed' and all(a['node_id'] != 'b' for a in finished['artifacts'])
    public = svc.acceptance_client.get('/api/workflow-runs/' + run['id'])
    assert public.status_code == 200
    assert 'PRIVATE-CANARY' not in public.text and str(outside) not in public.text
    missing = svc.acceptance_client.get(f'/api/workflow-runs/{run["id"]}/artifacts/escape')
    assert missing.status_code == 404 and 'PRIVATE-CANARY' not in missing.text


def test_art08_duplicate_display_names_use_unique_ids_and_exact_download_bytes(live):
    svc = live
    _, run = admit(svc, [producer('a', 'alpha'), producer('b', 'beta')])
    for nid in ('a', 'b'):
        assert svc.execute_node(run['id'], nid)['status'] == 'succeeded'
    result = svc.finish(run['id'])
    public = svc.acceptance_client.get('/api/workflow-runs/' + run['id']).json()
    assert len({a['id'] for a in public['artifacts']}) == 2
    assert [a['name'] for a in public['artifacts']] == ['same.txt', 'same.txt']
    for artifact in result['artifacts']:
        expected = b'alpha' if artifact['node_id'] == 'a' else b'beta'
        response = download(svc, run, artifact)
        assert response.status_code == 200 and response.content == expected
        assert hashlib.sha256(response.content).hexdigest() == artifact['sha256']
    assert svc.acceptance_client.get(f'/api/workflow-runs/{run["id"]}/artifacts/same.txt').status_code == 404
    assert all('path' not in a for a in public['artifacts'])


def test_art09_unauthorized_actor_cannot_download_known_artifact_or_cross_run(live):
    svc = live; client = svc.acceptance_client
    owner = client.get('/api/bootstrap').json()['user']['id']
    _, run = admit(svc, [producer(content='PRIVATE-AUTHORIZED-FILE')], allowed_user_ids=[owner])
    svc.execute_node(run['id'], 'a'); result = svc.finish(run['id']); item = result['artifacts'][0]
    _, other = admit(svc, [producer()]); svc.execute_node(other['id'], 'a'); svc.finish(other['id'])
    assert download(svc, other, item).status_code == 404
    assert client.post('/api/admin/users', json={'username': 'reader', 'password': 'artifact reader password', 'role': 'operator'}).status_code == 200
    with httpx.Client(base_url=client.base_url, trust_env=False) as actor:
        route = f'/api/workflow-runs/{run["id"]}/artifacts/{item["id"]}'
        assert actor.get(route).status_code == 401
        assert actor.post('/api/login', json={'username': 'reader', 'password': 'artifact reader password'}).status_code == 200
        denied = actor.get(route)
        assert denied.status_code in {403, 404}
        assert 'PRIVATE-AUTHORIZED-FILE' not in denied.text and str(svc.store.path) not in denied.text


def test_art05_collected_file_replaced_by_outside_symlink_is_not_opened_or_served(live, monkeypatch):
    svc = live
    _, run = admit(svc, [producer(content='same-bytes-cannot-authorize-outside-path')])
    svc.execute_node(run['id'], 'a'); result = svc.finish(run['id']); item = result['artifacts'][0]
    outside = svc.store.path / 'outside-download-canary.txt'
    outside.write_bytes(b'same-bytes-cannot-authorize-outside-path')
    path = Path(item['path']); path.unlink(); path.symlink_to(outside)
    before = outside.stat(); opened = []; original = Path.read_bytes
    def tracked(file):
        if file.resolve() == outside.resolve(): opened.append(str(file))
        return original(file)
    monkeypatch.setattr(Path, 'read_bytes', tracked)
    response = download(svc, run, item)
    assert response.status_code == 404 and 'same-bytes-cannot-authorize' not in response.text
    assert str(outside) not in response.text and opened == []
    assert outside.stat().st_mtime_ns == before.st_mtime_ns


@pytest.mark.parametrize('count,size,accepted', [(100, 1, True), (101, 1, False),
    (1, 100 * 1024 * 1024, True), (1, 100 * 1024 * 1024 + 1, False)])
def test_art10_real_worker_inclusive_file_and_byte_quotas(tmp_path, count, size, accepted):
    source = ('import os,json\nfrom pathlib import Path\na=Path(os.environ["SLEEP_IN_ARTIFACT_DIR"])\nitems=[]\n'
        f'for i in range({count}):\n p=a/str(i)\n with p.open("wb") as f: f.truncate({size})\n items.append({{"name":str(i)}})\n'
        'json.dump({"schemaVersion":1,"data":{},"artifacts":items},open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))')
    node = producer(); node['source'] = source
    attempt = tmp_path / 'attempt'
    if accepted:
        result = run_script(node, {}, attempt, tmp_path)
        assert result['status'] == 'succeeded' and len(result['output']['artifacts']) == count
        assert sum(a['size'] for a in result['output']['artifacts']) == count * size
        assert all(a['sha256'] == hashlib.sha256(Path(a['path']).read_bytes()).hexdigest() for a in result['output']['artifacts'])
    else:
        with pytest.raises(ValueError, match='Artifact quota exceeded'):
            run_script(node, {}, attempt, tmp_path)
    # Collection does not silently delete worker evidence; disposable fixture/retention owns cleanup.
    assert len(list((attempt / 'artifacts').iterdir())) == count
    assert (attempt / 'stdout.txt').exists() and (attempt / 'stderr.txt').exists()


def test_art11_tampered_download_and_downstream_materialization_fail_closed(live):
    svc = live
    consumer = {'id': 'consume', 'kind': 'python', 'source': 'def main(inputs): raise RuntimeError("must not execute")',
        'config': {}, 'inputs': {'file': {'source': 'artifact', 'node_id': 'a', 'name': 'same.txt'}}}
    _, run = admit(svc, [producer(content='original'), consumer], [{'source': 'a', 'target': 'consume'}])
    assert svc.execute_node(run['id'], 'a')['status'] == 'succeeded'
    item = svc.get_run(run['id'])['artifacts'][0]
    assert download(svc, run, item).content == b'original'
    Path(item['path']).write_bytes(b'tampered')
    response = download(svc, run, item)
    assert response.status_code == 422 and 'checksum' in response.text
    assert response.content != b'tampered' and str(svc.store.path) not in response.text
    state = svc.execute_node(run['id'], 'consume')
    assert state['status'] == 'failed' and 'checksum' in state['error'] and state['attempts'] == []
    assert svc.finish(run['id'])['status'] == 'failed'


@pytest.mark.parametrize('name', ['line\nbreak.txt', 'quoted"name.txt', '报告 文件.txt', '../folder/report.txt', r'folder\report.txt'])
def test_art13_download_filename_is_safe_while_display_name_is_preserved(live, name):
    svc = live
    _, run = admit(svc, [producer(name=name)])
    svc.execute_node(run['id'], 'a'); result = svc.finish(run['id']); item = result['artifacts'][0]
    response = download(svc, run, item)
    assert response.status_code == 200 and response.content == b'fixture-bytes'
    header = response.headers['content-disposition']
    assert '\r' not in header and '\n' not in header
    filename = unquote(header.split("filename*=utf-8''", 1)[1]) if "filename*=utf-8''" in header else header.split('filename=', 1)[1].strip('"')
    assert '/' not in filename and '\\' not in filename
    assert all(ord(character) >= 32 and ord(character) != 127 for character in filename)
    public = svc.acceptance_client.get('/api/workflow-runs/' + run['id']).json()
    assert public['artifacts'][0]['name'] == name and 'path' not in public['artifacts'][0]


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'), reason='BLOCKED_ENV: actual pinned n8n required')
@pytest.mark.parametrize('expiration', ['deleted', 'retention'])
def test_art12_expired_artifact_never_falls_back_to_same_named_new_run(live, expiration):
    from taskconsole.workflows_n8n import execute_graph
    from taskconsole.workflows_operations import WorkflowOperations
    svc = live
    wf, old = admit(svc, [producer(content='original-old-run')])
    execute_graph(svc, old['id']); old = svc.get_run(old['id'])
    assert old['status'] == 'succeeded' and old.get('n8n_execution_id')
    old_artifact = old['artifacts'][0]
    wf['nodes'][0] = producer(content='replacement-new-run')
    svc.save(wf, wf['id']); svc.publish(wf['id'])
    fresh = svc.admit(wf['id']); execute_graph(svc, fresh['id']); fresh = svc.get_run(fresh['id'])
    assert fresh['status'] == 'succeeded' and fresh.get('n8n_execution_id')
    assert fresh['artifacts'][0]['name'] == old_artifact['name']
    if expiration == 'deleted':
        Path(old_artifact['path']).unlink()
    else:
        with svc.store.transaction() as tx:
            record = tx.get('workflow_run', old['id'])
            record.update(created_at=(now() - timedelta(days=40)).isoformat(), finished_at=(now() - timedelta(days=40)).isoformat())
            tx.put('workflow_run', record)
        WorkflowOperations(svc.store).cleanup()
        assert svc.get_run(old['id'])['data_expired'] is True
        assert not Path(old_artifact['path']).exists()
    missing = download(svc, old, old_artifact)
    assert missing.status_code in {404, 410} and 'expired' in missing.text.lower()
    assert b'replacement-new-run' not in missing.content and str(svc.store.path) not in missing.text
    assert download(svc, fresh, fresh['artifacts'][0]).content == b'replacement-new-run'
    # Unsupported resume must not manufacture a new admission or substitute another run's artifact.
    response = svc.acceptance_client.post('/api/workflows/' + wf['id'] + '/run', json={'resume_from': 'a', 'run_id': old['id']})
    assert response.status_code == 422 and response.json()['detail']['code'] == 'unsupported_resume'
    with svc.store.transaction() as tx:
        assert len([r for r in tx.all('workflow_run') if r['workflow_id'] == wf['id']]) == 2


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'), reason='BLOCKED_ENV: actual pinned n8n required')
def test_con07_out12_concurrent_n8n_runs_isolate_equal_filenames_and_inputs(live):
    from taskconsole.workflows_n8n import execute_graph
    svc = live
    barrier = svc.store.path / 'concurrent-producer-barrier'; barrier.mkdir()
    source = ('import os,json,time\nfrom pathlib import Path\n'
        'value=json.load(open(os.environ["SLEEP_IN_INPUT_FILE"]))["label"]\n'
        f'barrier=Path({str(barrier)!r}); (barrier/value).write_text("ready")\n'
        'deadline=time.monotonic()+30\nwhile len(list(barrier.iterdir()))<2:\n'
        ' assert time.monotonic()<deadline,"second concurrent producer never started"\n time.sleep(.05)\n'
        'time.sleep(.2)\nPath(os.environ["SLEEP_IN_ARTIFACT_DIR"],"same.txt").write_text(value)\n'
        'json.dump({"schemaVersion":1,"data":{"label":value},"artifacts":[{"name":"same.txt"}]},open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))')
    runs = []
    for value in ('concurrent-alpha', 'concurrent-beta'):
        node = producer(); node['source'] = source
        node['inputs'] = {'label': {'source': 'constant', 'value': value}}
        _, run = admit(svc, [node]); runs.append((run, value))
    with ThreadPoolExecutor(2) as pool:
        futures = [pool.submit(execute_graph, svc, run['id']) for run, _ in runs]
        for result in futures:
            result.result(timeout=100)
    directories = set(); ids = set()
    for admitted, value in runs:
        run = svc.get_run(admitted['id'])
        assert run['status'] == 'succeeded' and run.get('n8n_execution_id')
        artifact = run['artifacts'][0]; ids.add(artifact['id'])
        directory = svc.store.path / 'workflow-runs' / run['id'] / 'a' / 'attempt-1'
        directories.add(directory)
        assert Path(artifact['path']) == directory / 'artifacts' / 'same.txt'
        assert json.loads((directory / 'input.json').read_text()) == {'label': value}
        assert json.loads((directory / 'output.json').read_text())['data'] == {'label': value}
        assert download(svc, run, artifact).content == value.encode()
    assert len(directories) == len(ids) == 2


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'), reason='BLOCKED_ENV: actual pinned n8n required')
def test_out12_retry_cannot_accept_previous_attempt_artifact_or_output(live):
    from taskconsole.workflows_n8n import execute_graph
    svc = live
    source = ('import os,json,sys\nfrom pathlib import Path\n'
        'a=Path(os.environ["SLEEP_IN_ARTIFACT_DIR"])\n'
        'if "attempt-1" in str(a):\n (a/"same.txt").write_text("stale-prior-attempt")\n'
        'json.dump({"schemaVersion":1,"data":{},"artifacts":[{"name":"same.txt"}]},open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))\n'
        'if "attempt-1" in str(a): sys.exit(1)\n')
    node = producer(); node.update(source=source, config={'entry_mode': 'file', 'retry': {'max_attempts': 2, 'safe_to_retry': True}})
    _, run = admit(svc, [node]); execute_graph(svc, run['id']); result = svc.get_run(run['id'])
    assert result['status'] == 'failed' and result.get('n8n_execution_id')
    state = result['nodes']['a']
    assert [a['status'] for a in state['attempts']] == ['failed', 'failed']
    assert 'node-local' in state['error'] and result['artifacts'] == []
    base = svc.store.path / 'workflow-runs' / run['id'] / 'a'
    assert (base / 'attempt-1' / 'artifacts' / 'same.txt').read_text() == 'stale-prior-attempt'
    assert not (base / 'attempt-2' / 'artifacts' / 'same.txt').exists()
    assert all((base / attempt / 'input.json').exists() and (base / attempt / 'output.json').exists()
               for attempt in ('attempt-1', 'attempt-2'))
