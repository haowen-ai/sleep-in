"""Disposable existing/migrated/restored account inventories and owner recovery."""
import copy
import getpass
import os
from pathlib import Path
import socket
import subprocess
import sys

import pytest
from fastapi.testclient import TestClient
from taskconsole.app import create_app
from taskconsole.auth import password_hash
from sqlalchemy import select
from taskconsole.store import Store
from taskconsole.workflows_backup import create_backup, restore_backup
from test_workflow_migration import fixture as legacy_fixture


BASE = 'http://127.0.0.1:18765'
OPTIONS = {'base_url': BASE, 'client': ('127.0.0.1', 50000)}


def local_env(monkeypatch):
    monkeypatch.setenv('SLEEP_IN_LOCAL', '1')
    monkeypatch.setenv('APP_HOST', '127.0.0.1')
    monkeypatch.setenv('APP_PORT', '18765')


def inventory(store):
    with store.transaction() as tx:
        return sorted(tx.all('user'), key=lambda row: row['id'])


@pytest.mark.parametrize('state_kind', ['existing', 'migrated', 'restored'])
def test_existing_migrated_restored_accounts_unchanged_across_two_starts(tmp_path, monkeypatch, state_kind):
    local_env(monkeypatch)
    state = tmp_path / 'source'
    if state_kind == 'migrated':
        store = legacy_fixture(state)
        from taskconsole.workflows_migration import WorkflowMigration
        migration = WorkflowMigration(store).convert('t1')
        assert migration['workflow_id']
    else:
        store = Store(state, 'sqlite:///' + str(state / 'console.sqlite'))
    users = [{'id': 'owner-existing', 'username': 'custom-owner', 'password': password_hash('synthetic-owner-existing'), 'role': 'admin', 'locale': 'en', 'enabled': True},
             {'id': 'disabled-reader', 'username': 'disabled-reader', 'password': password_hash('synthetic-disabled-reader'), 'role': 'operator', 'locale': 'zh', 'enabled': False}]
    with store.transaction() as tx:
        for user in users:
            tx.put('user', user)
    expected = copy.deepcopy(inventory(store))
    if state_kind == 'restored':
        blob = create_backup(store, 'synthetic-backup-password')
        state = tmp_path / 'restored'
        restored = Store(state, 'sqlite:///' + str(state / 'console.sqlite'))
        restore_backup(restored, blob, 'synthetic-backup-password')
        restored.engine.dispose()
    store.engine.dispose()
    for _ in range(2):
        app = create_app(state, 'sqlite:///' + str(state / 'console.sqlite'))
        try:
            with TestClient(app, **OPTIONS) as client:
                assert client.get('/api/bootstrap').json()['local_account'] is None
                assert inventory(app.state.store) == expected
                assert client.post('/api/login', json={'username': 'admin', 'password': 'sleepin123456'}).status_code == 401
                assert client.post('/api/login', json={'username': 'disabled-reader', 'password': 'synthetic-disabled-reader'}).status_code == 401
                assert client.post('/api/login', json={'username': 'custom-owner', 'password': 'synthetic-owner-existing'}).status_code == 200
                assert inventory(app.state.store) == expected
        finally:
            app.state.store.engine.dispose()


@pytest.mark.parametrize('change', ['owner_recovery', 'disabled', 'deleted', 'restored_marker_without_user'])
def test_initial_account_hint_never_resurrects_after_recovery_or_stale_marker(tmp_path, monkeypatch, capsys, change):
    local_env(monkeypatch)
    state = tmp_path / 'state'
    url = 'sqlite:///' + str(state / 'console.sqlite')
    app = create_app(state, url)
    with TestClient(app, **OPTIONS) as client:
        assert client.get('/api/bootstrap').json()['local_account']['username'] == 'admin'
        login = client.post('/api/login', json={'username': 'admin', 'password': 'sleepin123456'})
        assert login.status_code == 200
        user = inventory(app.state.store)[0]
        original_id = user['id']
        if change == 'owner_recovery':
            from taskconsole.__main__ import main
            monkeypatch.setenv('APP_STATE_DIR', str(state))
            monkeypatch.setenv('DATABASE_URL', url)
            monkeypatch.setattr(sys, 'argv', ['taskconsole', 'reset-password', 'admin'])
            monkeypatch.setattr(getpass, 'getpass', lambda prompt: 'synthetic-private-recovery-password')
            main()
            output = capsys.readouterr().out
            assert 'old sessions revoked' in output
            assert 'synthetic-private-recovery-password' not in output
            assert client.get('/api/tasks').status_code == 401
            assert client.post('/api/login', json={'username': 'admin', 'password': 'sleepin123456'}).status_code == 401
            assert client.post('/api/login', json={'username': 'admin', 'password': 'synthetic-private-recovery-password'}).status_code == 200
        else:
            with app.state.store.transaction() as tx:
                if change == 'disabled':
                    user['enabled'] = False
                    tx.put('user', user)
                else:
                    tx.remove('user', user['id'])
        assert client.get('/api/bootstrap').json()['local_account'] is None
        expected = inventory(app.state.store)
        if change == 'restored_marker_without_user':
            blob = create_backup(app.state.store, 'synthetic-backup-password')
    app.state.store.engine.dispose()
    if change == 'restored_marker_without_user':
        state = tmp_path / 'restored'
        url = 'sqlite:///' + str(state / 'console.sqlite')
        target = Store(state, url)
        restore_backup(target, blob, 'synthetic-backup-password')
        target.engine.dispose()
    for _ in range(2):
        app = create_app(state, url)
        try:
            with TestClient(app, **OPTIONS) as client:
                response = client.get('/api/bootstrap')
                assert response.json()['local_account'] is None
                assert 'synthetic-private-recovery-password' not in response.text
                assert inventory(app.state.store) == expected
                with app.state.store.transaction() as tx:
                    assert tx.get('meta', 'local-initial-account')['user_id'] == original_id
        finally:
            app.state.store.engine.dispose()


@pytest.mark.parametrize('changed', ['initial', 'disabled', 'rehashed_initial'])
def test_server_mode_refuses_public_local_account_before_listening_without_user_mutation(tmp_path, monkeypatch, changed):
    local_env(monkeypatch)
    state = tmp_path / 'state'
    url = 'sqlite:///' + str(state / 'console.sqlite')
    app = create_app(state, url)
    if changed != 'initial':
        with app.state.store.transaction() as tx:
            user = tx.all('user')[0]
            if changed == 'disabled':
                user['enabled'] = False
            else:
                user['password'] = password_hash('sleepin123456')
            tx.put('user', user)
    before = inventory(app.state.store)
    with app.state.store.transaction() as tx:
        records_before = tx.conn.execute(select(app.state.store.table).order_by(app.state.store.table.c.kind, app.state.store.table.c.id)).all()
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
    env = {**os.environ, 'SLEEP_IN_LOCAL': '0', 'APP_HOST': '127.0.0.1', 'APP_PORT': str(port), 'APP_STATE_DIR': str(state), 'DATABASE_URL': url}
    result = subprocess.run([sys.executable, '-m', 'taskconsole', 'serve'], env=env, capture_output=True, text=True, timeout=15)
    assert result.returncode != 0
    assert 'change the default password' in (result.stdout + result.stderr).lower()
    assert 'sleepin123456' not in result.stdout + result.stderr
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(('127.0.0.1', port)) != 0
    assert inventory(app.state.store) == before
    with app.state.store.transaction() as tx:
        assert tx.conn.execute(select(app.state.store.table).order_by(app.state.store.table.c.kind, app.state.store.table.c.id)).all() == records_before
    app.state.store.engine.dispose()


def listening_addresses(pid):
    """Inventory only this disposable child, never unrelated host services."""
    if sys.platform == 'darwin':
        result = subprocess.run(['/usr/sbin/lsof', '-nP', '-a', '-p', str(pid), '-iTCP', '-sTCP:LISTEN', '-Fn'], capture_output=True, text=True, timeout=5)
        assert result.returncode == 0, result.stderr
        return {line[1:] for line in result.stdout.splitlines() if line.startswith('n')}
    root = Path('/proc') / str(pid)
    inodes = set()
    for fd in (root / 'fd').iterdir():
        try:
            target = os.readlink(fd)
        except FileNotFoundError:
            continue
        if target.startswith('socket:['):
            inodes.add(target[8:-1])
    addresses = set()
    for protocol in ('tcp', 'tcp6'):
        for row in (root / 'net' / protocol).read_text().splitlines()[1:]:
            fields = row.split()
            if fields[3] != '0A' or fields[9] not in inodes:
                continue
            host, port = fields[1].split(':')
            raw = bytes.fromhex(host)
            if protocol == 'tcp':
                host = socket.inet_ntoa(raw[::-1])
            else:
                host = '[' + socket.inet_ntop(socket.AF_INET6, b''.join(raw[i:i+4][::-1] for i in range(0, 16, 4))) + ']'
            addresses.add(host + ':' + str(int(port, 16)))
    return addresses


def test_real_local_listener_and_forwarded_requests_never_expose_default_card(tmp_path, monkeypatch):
    import httpx
    import time
    local_env(monkeypatch)
    state = tmp_path / 'listener-state'
    url = 'sqlite:///' + str(state / 'console.sqlite')
    with socket.socket() as reserved:
        reserved.bind(('127.0.0.1', 0))
        port = reserved.getsockname()[1]
    env = {**os.environ, 'APP_PORT': str(port), 'APP_STATE_DIR': str(state), 'DATABASE_URL': url}
    child = subprocess.Popen([sys.executable, '-m', 'taskconsole', 'serve'], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    store = None
    try:
        with httpx.Client(base_url='http://127.0.0.1:' + str(port), timeout=2, trust_env=False) as client:
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                try:
                    if client.get('/healthz').status_code == 200:
                        break
                except httpx.TransportError:
                    pass
                assert child.poll() is None
                time.sleep(.05)
            else:
                raise AssertionError('Disposable local listener never became ready')
            assert listening_addresses(child.pid) == {'127.0.0.1:' + str(port)}
            assert client.get('/api/bootstrap').json()['local_account'] == {'username': 'admin', 'password': 'sleepin123456'}
            store = Store(state, url)
            with store.transaction() as tx:
                before = tx.conn.execute(select(store.table).order_by(store.table.c.kind, store.table.c.id)).all()
            for headers in [{'Forwarded': 'for=198.51.100.25;host=proxy.example;proto=https'},
                            {'X-Forwarded-For': '198.51.100.25'}, {'X-Forwarded-Host': 'proxy.example'},
                            {'X-Forwarded-Proto': 'https'}, {'X-Forwarded-Port': '443'},
                            {'Host': '198.51.100.25:' + str(port)}, {'Host': 'proxy.example:' + str(port)}]:
                response = client.get('/api/bootstrap', headers=headers)
                assert response.status_code == 403, response.text
                assert 'sleepin123456' not in response.text and 'local_account' not in response.text
            with store.transaction() as tx:
                assert tx.conn.execute(select(store.table).order_by(store.table.c.kind, store.table.c.id)).all() == before
            assert listening_addresses(child.pid) == {'127.0.0.1:' + str(port)}
    finally:
        child.terminate()
        try:
            child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=5)
        if store is not None:
            store.engine.dispose()
