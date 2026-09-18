"""Real local HTTP/account boundaries; no power or OS permission changes."""
import json
import os
from pathlib import Path
import subprocess
import pytest
from fastapi.testclient import TestClient
from taskconsole.app import create_app


@pytest.fixture
def local(tmp_path, monkeypatch):
    monkeypatch.setenv('SLEEP_IN_LOCAL', '1')
    monkeypatch.setenv('APP_HOST', '127.0.0.1')
    monkeypatch.setenv('APP_PORT', '18765')
    app = create_app(tmp_path, f'sqlite:///{tmp_path}/console.db')
    yield app
    app.state.store.engine.dispose()


@pytest.mark.parametrize('headers', [
    {'Origin': 'https://127.0.0.1:18765'},
    {'Origin': 'http://127.0.0.1:18766'},
    {'Origin': 'http://127.0.0.1:18765/path'},
    {'Origin': 'http://127.0.0.1:18765?query=1'},
    {'Host': '127.0.0.1:18766'},
    {'Host': '127.0.0.1'},
    {'Host': 'attacker@127.0.0.1:18765'},
    {'Host': 'localhost.attacker.example:18765'},
    {'Host': '127.0.0.1:18765/extra'},
    {'Host': '[::1]:18766'},
    {'X-Forwarded-Proto': 'http'},
    {'X-Forwarded-For': '127.0.0.1'},
])
def test_mac_au07_exact_origin_authority_rejected_without_state_changes(local, headers):
    with local.state.store.transaction() as tx:
        users = tx.all('user')
        sessions = tx.all('session')
    with TestClient(local, base_url='http://127.0.0.1:18765', client=('127.0.0.1', 50000)) as client:
        response = client.get('/api/bootstrap', headers=headers)
        assert response.status_code == 403
        assert 'local_account' not in response.json()
        assert client.post('/api/login', headers=headers, json={'username': 'admin', 'password': 'sleepin123456'}).status_code == 403
    with local.state.store.transaction() as tx:
        assert tx.all('user') == users
        assert tx.all('session') == sessions


@pytest.mark.parametrize('host', ['127.0.0.1:18765', 'localhost:18765', '[::1]:18765'])
def test_mac_au07_direct_loopback_authorities_allowed(local, host):
    with TestClient(local, base_url='http://127.0.0.1:18765', client=('127.0.0.1', 50000)) as client:
        response = client.get('/api/bootstrap', headers={'Host': host, 'Origin': 'http://' + host})
        assert response.status_code == 200
        assert response.json()['local_account']['username'] == 'admin'


def test_mac_au03_password_change_revokes_both_sessions_and_survives_restart(local, tmp_path):
    opts = {'base_url': 'http://127.0.0.1:18765', 'client': ('127.0.0.1', 50000)}
    creds = {'username': 'admin', 'password': 'sleepin123456'}
    with TestClient(local, **opts) as first, TestClient(local, **opts) as second:
        for client in (first, second):
            response = client.post('/api/login', json=creds)
            assert response.status_code == 200
            client.headers['X-CSRF-Token'] = response.json()['csrf']
            assert client.get('/api/tasks').status_code == 200
        replacement = 'synthetic-new-password-canary-123'
        assert first.post('/api/me/password', json={'current_password': creds['password'], 'password': replacement}).status_code == 200
        for client in (first, second):
            assert client.get('/api/tasks').status_code == 401
            assert client.get('/api/bootstrap').json()['local_account'] is None
            assert replacement not in client.get('/api/bootstrap').text
        assert second.post('/api/login', json=creds).status_code == 401
        assert second.post('/api/login', json={**creds, 'password': replacement}).status_code == 200
    restarted = create_app(tmp_path, f'sqlite:///{tmp_path}/console.db')
    try:
        with TestClient(restarted, **opts) as client:
            assert client.get('/api/bootstrap').json()['local_account'] is None
            assert client.post('/api/login', json=creds).status_code == 401
            assert client.post('/api/login', json={**creds, 'password': replacement}).status_code == 200
    finally:
        restarted.state.store.engine.dispose()


@pytest.mark.parametrize('os_name,arch,version,free,error', [
    ('Linux', 'arm64', '15.0', '9999999', 'requires macOS'),
    ('Darwin', 'x86_64', '15.0', '9999999', 'Apple silicon'),
    ('Darwin', 'arm64', '12.0', '9999999', 'macOS 13'),
    ('Darwin', 'arm64', '15.0', '1024', '2 GiB'),
])
def test_mac_in02_unsupported_or_full_disk_never_starts_runtime(tmp_path, os_name, arch, version, free, error):
    fake = tmp_path / 'os-boundary'
    fake.mkdir()
    scripts = {'uname': f'if [ "$1" = "-s" ]; then echo {os_name}; else echo {arch}; fi',
               'sw_vers': f'echo {version}',
               'df': f'echo "Filesystem 1024-blocks Used Available Capacity Mounted"; echo "test 100 0 {free} 0% /"',
               'curl': 'echo unexpected-network >&2; exit 91'}
    for name, source in scripts.items():
        path = fake / name
        path.write_text('#!/bin/sh\n' + source + '\n')
        path.chmod(0o755)
    install = tmp_path / 'installation'
    launcher = Path(__file__).resolve().parents[1] / 'launch-mac.command'
    result = subprocess.run(['/bin/bash', str(launcher), '--install-only'], capture_output=True, text=True,
        env={**os.environ, 'SLEEP_IN_INSTALL_DIR': str(install), 'PATH': str(fake) + ':/usr/bin:/bin'}, timeout=10)
    assert result.returncode != 0
    progress = install / 'install-progress.json'
    message = result.stdout + result.stderr + (progress.read_text() if progress.exists() else '')
    assert error in message
    assert 'unexpected-network' not in message
    assert not (install / 'venv').exists()
    assert not (install / 'state/local-status.json').exists()


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_TEST_N8N_COMMAND'), reason='Actual n8n fixture required')
def test_mac_in07_ten_concurrent_launches_reuse_one_real_service(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import socket
    import sys
    import time
    from taskconsole.local import write_json, request_stop, status
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    write_json(tmp_path / 'local-config.json', {'app_port': port,
        'n8n_command': json.loads(os.environ['SLEEP_IN_TEST_N8N_COMMAND']), 'disable_power_assertion': True})
    def launch(_):
        return subprocess.run([sys.executable, '-m', 'taskconsole.local', '--state', str(tmp_path), 'start', '--explicit'],
                              capture_output=True, text=True, timeout=100)
    try:
        with ThreadPoolExecutor(max_workers=10) as pool:
            responses = list(pool.map(launch, range(10)))
        assert all(r.returncode == 0 for r in responses), [(r.returncode, r.stderr) for r in responses]
        states = [json.loads(r.stdout) for r in responses]
        assert {s['state'] for s in states} == {'running'}
        assert len({s['pid'] for s in states}) == 1
        assert len({s['generation'] for s in states}) == 1
        assert len({tuple(sorted(s['children'].items())) for s in states}) == 1
        assert all(s['components']['power'] == 'disabled-for-test' for s in states)
        from taskconsole.store import Store
        store = Store(tmp_path, f'sqlite:///{tmp_path}/console.db')
        try:
            with store.transaction() as tx:
                assert tx.all('workflow_run') == []
        finally:
            store.engine.dispose()
    finally:
        request_stop(tmp_path, 'cancel')
        deadline = time.monotonic() + 25
        while status(tmp_path)['state'] != 'stopped' and time.monotonic() < deadline:
            time.sleep(.2)
        assert status(tmp_path)['state'] == 'stopped'


def test_mac_in03_busy_port_is_rejected_before_any_runtime_launch(tmp_path):
    import socket
    import sys
    from taskconsole.local import write_json, status
    with socket.socket() as owner:
        owner.bind(('127.0.0.1', 0))
        owner.listen(1)
        port = owner.getsockname()[1]
        # If the occupied-port check fails, this invalid runtime fails differently.
        write_json(tmp_path / 'local-config.json', {'app_port': port,
            'n8n_command': ['/no-such-runtime'], 'disable_power_assertion': True})
        result = subprocess.run([sys.executable, '-m', 'taskconsole.local', '--state', str(tmp_path), 'serve'],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode != 0
        assert 'Address already in use' in result.stderr
        assert 'No such file' not in result.stderr
        assert status(tmp_path)['state'] == 'stopped'
        assert not (tmp_path / 'console.db').exists()
        assert not (tmp_path / 'local-service.log').exists()
        assert owner.getsockname()[1] == port
