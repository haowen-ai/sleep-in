"""Two real CLI graphs must not inherit or mutate an existing n8n installation."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess

from test_acceptance_data import live
from test_acceptance_branches import native, calls, requires_n8n
from taskconsole.workflows_n8n import command, execute_graph


def manifest(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


@requires_n8n
def test_two_actual_graphs_isolate_existing_n8n_state_database_and_broker_configuration(live, monkeypatch):
    existing = live.store.path / 'unrelated-existing-n8n'
    existing.mkdir()
    definition = live.store.path / 'unrelated-workflow.json'
    definition.write_text(json.dumps({'id': 'unrelatedFixture', 'name': 'Unrelated fixture', 'active': False,
        'nodes': [{'id': 'start', 'name': 'Start', 'type': 'n8n-nodes-base.manualTrigger',
                   'typeVersion': 1, 'position': [0, 0], 'parameters': {}}],
        'connections': {}, 'settings': {'executionOrder': 'v1'}}))
    argv = command()
    env = {k: v for k, v in os.environ.items() if not k.startswith(('DB_', 'N8N_', 'QUEUE_', 'EXECUTIONS_'))}
    env.update(N8N_USER_FOLDER=str(existing), DB_TYPE='sqlite', N8N_DIAGNOSTICS_ENABLED='false',
        N8N_RUNNERS_ENABLED='false', N8N_ENCRYPTION_KEY='owned-unrelated-installation-key',
        N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS='true', N8N_VERSION_NOTIFICATIONS_ENABLED='false')
    env['PATH'] = str(Path(argv[0]).parent) + os.pathsep + env.get('PATH', '')
    result = subprocess.run([*argv, 'import:workflow', '--input=' + str(definition)], env=env,
        capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr + result.stdout
    original_db = existing / '.n8n' / 'database.sqlite'
    assert original_db.is_file()
    with sqlite3.connect('file:' + str(original_db) + '?mode=ro', uri=True) as db:
        assert db.execute('SELECT id FROM workflow_entity').fetchall() == [('unrelatedFixture',)]
    before = manifest(existing)
    jobs = []
    for label in ['one', 'two']:
        node = native(label, body='return {"identity":' + repr(label) + '}')
        wf = live.save({'name': 'Isolated ' + label, 'nodes': [node], 'edges': []})
        live.publish(wf['id'])
        jobs.append(live.admit(wf['id']))
    invocations = []
    popen = subprocess.Popen
    def observe(args, *positional, **kwargs):
        if len(args) > len(argv) and list(args[:len(argv)]) == argv:
            invocations.append((list(args), dict(kwargs['env']), Path(kwargs['cwd'])))
        return popen(args, *positional, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', observe)
    with socket.socket() as occupied:
        occupied.bind(('127.0.0.1', 0)); occupied.listen()
        inherited_port = str(occupied.getsockname()[1])
        monkeypatch.setenv('N8N_USER_FOLDER', str(existing))
        monkeypatch.setenv('N8N_RUNNERS_BROKER_PORT', inherited_port)
        monkeypatch.setenv('N8N_ENCRYPTION_KEY', 'owned-unrelated-installation-key')
        monkeypatch.setenv('DB_TYPE', 'postgresdb')
        monkeypatch.setenv('DB_POSTGRESDB_HOST', 'must-never-contact.invalid')
        with ThreadPoolExecutor(2) as pool:
            list(pool.map(lambda run: execute_graph(live, run['id']), jobs))
        assert occupied.getsockname()[1] == int(inherited_port)
    assert manifest(existing) == before
    executions = [item for item in invocations if 'execute' in item[0]]
    assert len(executions) == 2
    assert len({e['N8N_USER_FOLDER'] for _, e, _ in executions}) == 2
    assert len({e['N8N_RUNNERS_BROKER_PORT'] for _, e, _ in executions}) == 2
    for args, child_env, cwd in executions:
        assert child_env['N8N_USER_FOLDER'] == str(cwd / 'n8n')
        assert child_env['N8N_USER_FOLDER'] != str(existing)
        assert child_env['N8N_RUNNERS_BROKER_PORT'] != inherited_port
        assert child_env['N8N_RUNNERS_ENABLED'] == 'false' and child_env['DB_TYPE'] == 'sqlite'
        assert 'DB_POSTGRESDB_HOST' not in child_env
        with sqlite3.connect('file:' + str(cwd / 'n8n' / '.n8n' / 'database.sqlite') + '?mode=ro', uri=True) as db:
            assert db.execute('SELECT workflowId FROM execution_entity').fetchall() == [(cwd.name,)]
            assert db.execute('SELECT id FROM workflow_entity').fetchall() == [(cwd.name,)]
    for label, job in zip(['one', 'two'], jobs):
        result = live.get_run(job['id'])
        assert result['status'] == 'succeeded' and result['n8n_execution_id'] and result['adapter_finished_at']
        assert result['nodes'][label]['output']['data'] == {'identity': label}
        calls(live, result, label, 1)
