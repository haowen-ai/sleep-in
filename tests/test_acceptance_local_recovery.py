"""Real isolated supervisor/n8n lifecycle trials; no power or host network changes."""
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import text
from taskconsole.local import read_json, request_stop, status, write_json
from taskconsole.store import Store
from taskconsole.workflows import WorkflowService


pytestmark = pytest.mark.skipif(not os.environ.get('SLEEP_IN_TEST_N8N_COMMAND'),
    reason='BLOCKED_ENV: actual pinned Node/n8n supervisor fixture required')


def until(predicate, timeout=70):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.15)
    raise AssertionError('Lifecycle condition did not become true before deadline')


def alive(pid):
    proc = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'stat='],
                          capture_output=True, text=True, timeout=3)
    return proc.returncode == 0 and bool(proc.stdout.strip()) and not proc.stdout.strip().startswith('Z')


@pytest.fixture
def supervisor(tmp_path, monkeypatch):
    command = json.loads(os.environ['SLEEP_IN_TEST_N8N_COMMAND'])
    monkeypatch.setenv('PATH', str(Path(command[0]).parent) + os.pathsep + os.environ.get('PATH', ''))
    state = tmp_path / 'installation' / 'state'
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0)); port = reserved.getsockname()[1]
    write_json(state / 'local-config.json', {'app_port': port,
        'n8n_command': command, 'disable_power_assertion': True})
    def launch(*args):
        result = subprocess.run([sys.executable, '-m', 'taskconsole.local', '--state', str(state), 'start', *args],
                                capture_output=True, text=True, timeout=100)
        assert result.returncode == 0, result.stderr
        value = json.loads(result.stdout)
        assert value['state'] == 'running' and value['components']['worker'] == 'ready', value
        assert value['components']['power'] == 'disabled-for-test' and not value['assertion']
        return value
    store = None
    try:
        initial = launch('--explicit')
        store = Store(state, 'sqlite:///' + str(state / 'console.db'))
        svc = WorkflowService(store)
        # F1 software fixture: fresh local administrator and published real three-node example.
        template = svc.save(svc.templates()[0]); svc.publish(template['id'])
        with httpx.Client(base_url=initial['url'], timeout=10, trust_env=False) as client:
            response = client.post('/api/login', json={'username': 'admin', 'password': 'sleepin123456'})
            assert response.status_code == 200
            client.headers['X-CSRF-Token'] = response.json()['csrf']
            yield SimpleNamespace(state=state, svc=svc, store=store, initial=initial,
                                  launch=launch, client=client, template=template)
    finally:
        request_stop(state, 'cancel')
        until(lambda: status(state)['state'] == 'stopped', timeout=30)
        if store:
            store.engine.dispose()


def publish(s, source):
    wf = s.svc.save({'name': 'Isolated lifecycle effect', 'timeout': 180,
        'nodes': [{'id': 'source', 'kind': 'python', 'source': source, 'inputs': {}, 'config': {}}],
        'edges': [], 'notifications': {'channel_ids': []}})
    s.svc.publish(wf['id'])
    return wf


def finished(s, rid):
    return until(lambda: (run if (run := s.svc.get_run(rid))['status'] in
        {'succeeded', 'failed', 'cancelled', 'timed_out', 'partial'} and run.get('adapter_finished_at') else None))


def healthy_same(s):
    current = status(s.state)
    assert current['state'] == 'running' and current['pid'] == s.initial['pid']
    assert current['generation'] == s.initial['generation']
    assert current['children'] == s.initial['children']
    assert current['running_preference'] is True
    assert current['components']['power'] == 'disabled-for-test'
    health = s.client.get('/api/workflow-health')
    assert health.status_code == 200 and health.json()['status'] == 'ready'
    assert health.json()['instance_id'] == current['generation']


def test_mac_rc09_loopback_outage_preserves_supervisor_and_local_work(supervisor):
    s = supervisor; calls = []
    class Source(BaseHTTPRequestHandler):
        def do_GET(self):
            calls.append(self.path)
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"available":true}')
        def log_message(self, *args):
            pass
    # Bound but not listening: controlled unavailability, without changing networking.
    server = HTTPServer(('127.0.0.1', 0), Source, bind_and_activate=False)
    server.server_bind(); thread = None
    attempts = s.state / 'network-attempts.txt'
    source = ('import json\nfrom pathlib import Path\nfrom urllib.request import build_opener,ProxyHandler\n'
        'urlopen=build_opener(ProxyHandler({})).open\n'
        'def main(inputs):\n'
        f' with Path({str(attempts)!r}).open("a") as ledger: ledger.write("attempt\\n")\n'
        f' with urlopen("http://127.0.0.1:{server.server_address[1]}/source",timeout=1) as reply: return json.load(reply)\n')
    try:
        wf = publish(s, source)
        failed = s.svc.admit(wf['id'], key='network-outage')
        local = s.svc.admit(s.template['id'], key='independent-local')
        failure = finished(s, failed['id']); success = finished(s, local['id'])
        assert failure['status'] == 'failed'
        assert any(message in failure['nodes']['source']['stderr'] for message in ('Connection refused', 'timed out'))
        assert len(failure['nodes']['source']['attempts']) == 1
        assert success['status'] == 'succeeded' and success.get('n8n_execution_id')
        assert success['nodes']['summary']['output']['data']['summary'] == {'count': 3, 'total': '30.75'}
        assert calls == [] and attempts.read_text().splitlines() == ['attempt']
        healthy_same(s)
        server.server_activate()
        thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
        # A healthy independent run after restoration proves ongoing admission, while the failed
        # network run and its idempotency key cannot silently replay.
        assert s.svc.admit(wf['id'], key='network-outage')['id'] == failed['id']
        assert finished(s, s.svc.admit(s.template['id'], key='local-after-restore')['id'])['status'] == 'succeeded'
        assert calls == [] and attempts.read_text().splitlines() == ['attempt']
        recovered = finished(s, s.svc.admit(wf['id'], key='explicit-after-restoration')['id'])
        assert recovered['status'] == 'succeeded' and recovered.get('n8n_execution_id')
        assert recovered['nodes']['source']['output']['data'] == {'available': True}
        assert calls == ['/source'] and attempts.read_text().splitlines() == ['attempt', 'attempt']
        retained = s.svc.get_run(failed['id'])
        assert retained['status'] == 'failed' and retained['nodes'] == failure['nodes']
        healthy_same(s)
    finally:
        if thread:
            server.shutdown(); thread.join(timeout=3)
        server.server_close()


def test_mac_bg14_repeated_real_health_probes_have_no_business_effects(supervisor):
    s = supervisor; requests = []
    class Receiver(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path); self.send_response(204); self.end_headers()
        def log_message(self, *args):
            pass
    server = HTTPServer(('127.0.0.1', 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    ledger = s.state / 'forbidden-probe-effect.txt'
    try:
        publish(s, 'from pathlib import Path\nfrom urllib.request import build_opener,ProxyHandler\n'
            'urlopen=build_opener(ProxyHandler({})).open\ndef main(inputs):\n'
            f' Path({str(ledger)!r}).write_text("business effect")\n'
            f' urlopen("http://127.0.0.1:{server.server_port}/effect",timeout=2).read()\n return {{}}')
        from taskconsole.workflows_sql import create_connection
        connection = create_connection(s.store, {'name': 'Probe business ledger', 'dialect': 'sqlite',
            'config': {'synthetic': True}, 'write_enabled': True})
        sql_workflow = s.svc.save({'name': 'Never execute from health', 'nodes': [{'id': 'write', 'kind': 'sql',
            'source': "UPDATE orders SET amount='999' WHERE order_id='A001'", 'inputs': {},
            'config': {'dialect': 'sqlite', 'mode': 'write', 'connection_id': connection['id']}}], 'edges': []})
        s.svc.publish(sql_workflow['id'])
        business_db = s.state / 'workflow-connections' / (connection['id'] + '.sqlite')
        with sqlite3.connect(business_db) as db:
            db.execute('CREATE TABLE acceptance_business_writes(operation TEXT)')
            for operation in ('INSERT', 'UPDATE', 'DELETE'):
                db.execute(f'CREATE TRIGGER acceptance_business_{operation.lower()} AFTER {operation} ON orders '
                    f"BEGIN INSERT INTO acceptance_business_writes VALUES ('{operation}'); END")
            business_before = db.execute('SELECT * FROM orders ORDER BY order_id').fetchall()
        with s.store.engine.begin() as conn:
            conn.exec_driver_sql('CREATE TABLE acceptance_mutations(kind TEXT, record_id TEXT, operation TEXT)')
            for operation in ('INSERT', 'UPDATE', 'DELETE'):
                row = 'OLD' if operation == 'DELETE' else 'NEW'
                conn.exec_driver_sql(f'CREATE TRIGGER acceptance_probe_{operation.lower()} AFTER {operation} ON console_records '
                    f'BEGIN INSERT INTO acceptance_mutations VALUES ({row}.kind,{row}.id,\'{operation}\'); END')
            before = conn.execute(text('SELECT kind,id,payload FROM console_records WHERE kind != :meta'), {'meta': 'meta'}).all()
        for _ in range(24):
            response = s.client.get('/healthz')
            assert response.status_code == 200 and response.json()['instance_id'] == s.initial['generation']
            healthy_same(s)
            assert status(s.state)['next_scheduled'] is None
        with s.store.engine.connect() as conn:
            after = conn.execute(text('SELECT kind,id,payload FROM console_records WHERE kind != :meta'), {'meta': 'meta'}).all()
            mutations = conn.execute(text('SELECT kind,record_id,operation FROM acceptance_mutations')).all()
        assert after == before
        # The independent supervisor/worker still refresh their documented heartbeats/cleanup.
        assert all(kind == 'meta' and rid in {'workflow_worker', 'workflow_cleanup'} for kind, rid, _ in mutations), mutations
        with s.store.transaction() as tx:
            assert tx.all('workflow_run') == tx.all('workflow_occurrence') == tx.all('execution') == []
        with sqlite3.connect(business_db) as db:
            assert db.execute('SELECT * FROM orders ORDER BY order_id').fetchall() == business_before
            assert db.execute('SELECT * FROM acceptance_business_writes').fetchall() == []
        assert requests == [] and not ledger.exists()
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_mac_rc01_supervisor_sigkill_reclaims_owned_workers_without_replaying_effect(supervisor):
    s = supervisor; ledger = s.state / 'crash-effect.txt'
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(180)'],
                                 cwd=s.state.parent, start_new_session=True)
    try:
        wf = publish(s, 'import time\nfrom pathlib import Path\ndef main(inputs):\n'
            f' with Path({str(ledger)!r}).open("a") as target: target.write("committed-once\\n"); target.flush()\n'
            ' time.sleep(150)\n return {"done":True}')
        run = s.svc.admit(wf['id'], key='crash-effect-once')
        running = until(lambda: (value if (value := s.svc.get_run(run['id']))['nodes']['source'].get('worker_pid')
            and ledger.exists() else None))
        worker_pid = running['nodes']['source']['worker_pid']
        adapter_pid = running['adapter_pid']
        with s.store.transaction() as tx:
            coordinator_worker_pid = tx.get('meta', 'workflow_worker')['pid']
        assert alive(worker_pid) and alive(adapter_pid) and unrelated.poll() is None
        before = status(s.state)
        os.kill(before['pid'], signal.SIGKILL)
        until(lambda: not alive(before['pid']), timeout=20)
        until(lambda: all(not alive(pid) for pid in before['children'].values()), timeout=30)
        assert status(s.state)['state'] == 'stopped'
        assert read_json(s.state / 'local-preferences.json')['running'] is True
        replacement = s.launch()
        assert replacement['pid'] != before['pid'] and replacement['generation'] != before['generation']
        assert replacement['recovery']['previous_seen'] >= before['started_at']
        assert replacement['recovery']['restored_at'] > replacement['recovery']['previous_seen']
        assert s.launch()['pid'] == replacement['pid']
        until(lambda: all(not alive(pid) for pid in (worker_pid, adapter_pid, coordinator_worker_pid)), timeout=15)
        final = finished(s, run['id'])
        assert final['status'] in {'failed', 'cancelled'}
        assert len(final['nodes']['source']['attempts']) == 1
        assert ledger.read_text().splitlines() == ['committed-once']
        assert s.svc.admit(wf['id'], key='crash-effect-once')['id'] == run['id']
        successful = finished(s, s.svc.admit(s.template['id'], key='replacement-healthy')['id'])
        assert successful['status'] == 'succeeded' and successful.get('n8n_execution_id')
        assert ledger.read_text().splitlines() == ['committed-once']
        assert unrelated.poll() is None
        with s.store.transaction() as tx:
            assert len([r for r in tx.all('workflow_run') if r['workflow_id'] == wf['id']]) == 1
    finally:
        unrelated.terminate(); unrelated.wait(timeout=5)
