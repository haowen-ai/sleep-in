"""Disposable PostgreSQL/MySQL/Oracle roles, typed rows and commit-loss trials.

D tests require SLEEP_IN_EXTERNAL_SQL_TESTS=1 and the pinned compose fixtures.
Only unique synthetic users/tables are modified; existing fixture passwords stay intact.
The local SQLite cases exercise the same real-commit fault boundary through n8n.
"""
import copy
from datetime import datetime, timezone
from decimal import Decimal
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import uuid

import pytest
from test_workflow_api_contract import client, admin
from test_workflow_execution_n8n_complete import live
from test_workflow_external_sql import receiver, fixture_config, wait_for_oracle_fixture_ddl
from taskconsole.workflows_sql import create_connection, connect, execute_sql, sql_text
from taskconsole.workflows_n8n import execute_graph
from taskconsole.workflows import public_run


ENGINE = pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'), reason='BLOCKED_ENV: actual n8n command required')


def sql_node(cid, dialect, source, mode='query', retry=None):
    return {'id': 'sql', 'kind': 'sql', 'source': source, 'inputs': {},
        'config': {'connection_id': cid, 'dialect': dialect, 'mode': mode, 'timeout': 10,
                   **({'retry': retry} if retry else {})}}


def record(store, cid):
    with store.transaction() as tx:
        return tx.get('connection', cid)


def clone_connection(store, receiver, config=None):
    dialect = receiver[1]['config']['dialect']
    return create_connection(store, {'name': 'Disposable receiver', 'dialect': dialect,
        'config': config or fixture_config(dialect), 'write_enabled': True})


def run_fault_trial(live, connection, table, monkeypatch, boundary):
    """Actual driver connection/commit, with only the transport outcome faulted."""
    import taskconsole.workflows_sql as sql
    original_connect = sql.connect
    events = []
    first = True
    def fault_connect(store, saved, write=False, timeout=None):
        nonlocal first
        db = original_connect(store, saved, write, timeout)
        if not write or not first:
            return db
        first = False
        if boundary == 'before_statement':
            db.close()
            check = original_connect(store, saved)
            try:
                cur = check.cursor()
                cur.execute(f"SELECT COUNT(*) FROM {table} WHERE order_id='effect-001'")
                assert cur.fetchone()[0] == 0
                cur.close()
                check.rollback()
            finally:
                check.close()
            events.append('disconnected_before_statement')
            raise ConnectionResetError('Synthetic disconnect before statement')
        class LostCommitAcknowledgement:
            def __getattr__(self, key):
                return getattr(db, key)
            def commit(self):
                db.commit()
                events.append('actual_commit_completed')
                raise ConnectionResetError('Synthetic lost COMMIT acknowledgement')
        return LostCommitAcknowledgement()
    monkeypatch.setattr(sql, 'connect', fault_connect)
    node = sql_node(connection['id'], connection['dialect'],
        f"INSERT INTO {table}(order_id) VALUES ('effect-001')", 'write',
        {'max_attempts': 3, 'safe_to_retry': True, 'idempotent': True, 'delay_seconds': 0})
    graph = {'name': 'Commit uncertainty ' + boundary, 'timeout': 90, 'nodes': [node], 'edges': []}
    workflow = live.save(graph)
    live.publish(workflow['id'])
    admitted = live.admit(workflow['id'], key='single-logical-effect')
    execute_graph(live, admitted['id'])
    result = public_run(live.get_run(admitted['id']))
    state = result['nodes']['sql']
    # Read independently with a fresh actual driver connection, outside the fault wrapper.
    db = original_connect(live.store, record(live.store, connection['id']))
    try:
        cursor = db.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE order_id='effect-001'")
        assert cursor.fetchone()[0] == 1
        cursor.close()
        db.rollback()
    finally:
        db.close()
    if boundary == 'after_commit':
        assert events == ['actual_commit_completed']
        assert result['status'] == 'failed'
        assert len(state['attempts']) == 1, state['attempts']
        assert state['retryable'] is False
        assert state['reason'] == 'commit_outcome_uncertain'
        assert 'inspect' in state['error'].lower()
        assert result['side_effects_uncertain'] is True
        assert result['review_reason']['code'] == 'check_external_effects'
        execute_graph(live, admitted['id'])
        assert live.admit(workflow['id'], key='single-logical-effect')['id'] == admitted['id']
        assert len(live.get_run(admitted['id'])['nodes']['sql']['attempts']) == 1
    else:
        assert events == ['disconnected_before_statement']
        assert result['status'] == 'succeeded', result
        assert [attempt['status'] for attempt in state['attempts']] == ['failed', 'succeeded']
        assert result['side_effects_uncertain'] is False
    assert result['n8n_execution_id'] and result['graph_execution_id']


@ENGINE
@pytest.mark.parametrize('boundary', ['before_statement', 'after_commit'])
def test_sql21_local_real_commit_transport_boundary(live, monkeypatch, boundary):
    connection = create_connection(live.store, {'name': 'Local commit oracle', 'dialect': 'sqlite', 'config': {}, 'write_enabled': True})
    db = connect(live.store, record(live.store, connection['id']), True)
    try:
        db.execute('CREATE TABLE effects(order_id TEXT PRIMARY KEY)')
        db.commit()
    finally:
        db.close()
    run_fault_trial(live, connection, 'effects', monkeypatch, boundary)


@ENGINE
@pytest.mark.parametrize('boundary', ['before_statement', 'after_commit'])
def test_sql21_external_actual_commit_and_pre_statement_disconnect(receiver, live, monkeypatch, boundary):
    connection = clone_connection(live.store, receiver)
    run_fault_trial(live, connection, receiver[2], monkeypatch, boundary)


@pytest.fixture
def disposable_role(receiver):
    """Grant only SELECT to a new unique principal, using synthetic fixture admins."""
    store, node, table, connection = receiver
    dialect = node['config']['dialect']
    config = fixture_config(dialect)
    name = 'si_role_' + uuid.uuid4().hex[:12]
    original_password = 'SiFixture_' + uuid.uuid4().hex[:16]
    replacement = 'SiRotated_' + uuid.uuid4().hex[:16]
    owner = config.get('user', 'sleepin')
    qualified = ('public.' if dialect == 'postgresql' else config.get('database', 'sleepin') + '.' if dialect == 'mysql' else owner + '.') + table
    admin_db = None
    if dialect == 'oracle':
        import oracledb
        admin_db = oracledb.connect(user='system', password='sleepin_fixture_root', dsn=config['dsn'])
    elif dialect == 'postgresql':
        admin_db = connect(store, connection, True)
    def admin_sql(statement):
        if dialect == 'mysql':
            command = ['docker', 'compose', '-f', 'deploy/sql-matrix-compose.yaml', 'exec', '-T', 'mysql',
                'mysql', '--user=root', '--password=sleepin_fixture_root', '--execute', statement]
            result = subprocess.run(command, cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True, timeout=30)
            assert result.returncode == 0, result.stderr
        else:
            with admin_db.cursor() as cur:
                cur.execute(statement)
            admin_db.commit()
    def rotate(password):
        if dialect == 'postgresql':
            admin_sql(f'ALTER ROLE "{name}" PASSWORD \'{password}\'')
        elif dialect == 'mysql':
            admin_sql(f"ALTER USER '{name}'@'%' IDENTIFIED BY '{password}'")
        else:
            admin_sql(f'ALTER USER {name} IDENTIFIED BY "{password}"')
    created = False
    try:
        if dialect == 'postgresql':
            admin_sql(f'CREATE ROLE "{name}" LOGIN PASSWORD \'{original_password}\'')
            created = True
            admin_sql(f'GRANT USAGE ON SCHEMA public TO "{name}"')
            admin_sql(f'GRANT SELECT ON {qualified} TO "{name}"')
        elif dialect == 'mysql':
            admin_sql(f"CREATE USER '{name}'@'%' IDENTIFIED BY '{original_password}'")
            created = True
            admin_sql(f"GRANT SELECT ON {qualified} TO '{name}'@'%'")
        else:
            admin_sql(f'CREATE USER {name} IDENTIFIED BY "{original_password}"')
            created = True
            admin_sql(f'GRANT CREATE SESSION TO {name}')
            admin_sql(f'GRANT SELECT ON {qualified} TO {name}')
        role_config = {**config, 'user': name, 'password': original_password}
        yield {'config': role_config, 'table': qualified, 'rotate': rotate,
               'old_password': original_password, 'new_password': replacement, 'dialect': dialect}
    finally:
        try:
            if created:
                if admin_db is not None:
                    admin_db.rollback()
                if dialect == 'postgresql':
                    admin_sql(f'DROP OWNED BY "{name}"')
                    admin_sql(f'DROP ROLE "{name}"')
                elif dialect == 'mysql':
                    admin_sql(f"DROP USER IF EXISTS '{name}'@'%'")
                else:
                    admin_sql(f'DROP USER {name} CASCADE')
        finally:
            if admin_db is not None:
                admin_db.close()


def create_api_connection(client, role):
    admin(client)
    response = client.post('/api/connections', json={'name': 'Read-only DB principal', 'dialect': role['dialect'],
        'config': role['config'], 'write_enabled': True})
    assert response.status_code == 200, response.text
    return response.json()


def test_sql12_actual_read_only_principal_cannot_write_even_when_connection_flag_allows(client, receiver, disposable_role):
    role = disposable_role
    connection = create_api_connection(client, role)
    tested = client.post('/api/connections/' + connection['id'] + '/test')
    assert tested.status_code == 200 and tested.json()['ok'] is True
    assert tested.json()['message'] == 'Read-only connection test succeeded'
    node = sql_node(connection['id'], role['dialect'], f"INSERT INTO {role['table']}(order_id) VALUES ('forbidden-effect')", 'write')
    with pytest.raises(Exception) as denied:
        execute_sql(client.app.state.store, node, {}, 'readonly-role-test')
    if role['dialect'] == 'postgresql':
        assert denied.value.sqlstate == '42501'
    elif role['dialect'] == 'mysql':
        assert denied.value.args[0] in {1142, 1143}
    else:
        assert denied.value.args[0].code == 1031
    node.update(source='SELECT COUNT(*) AS "n" FROM ' + role['table'])
    node['config']['mode'] = 'query'
    rows = execute_sql(client.app.state.store, node, {}, 'readonly-role-test')['output']['data']['rows']
    assert int(rows[0]['n']) == 2
    assert role['old_password'] not in client.get('/api/connections').text


def test_sql17_actual_password_rotation_after_publication_uses_new_authorized_revision(client, receiver, disposable_role):
    role = disposable_role
    connection = create_api_connection(client, role)
    node = sql_node(connection['id'], role['dialect'], 'SELECT COUNT(*) AS "n" FROM ' + role['table'])
    created = client.post('/api/workflows', json={'name': 'Rotated credentials', 'nodes': [node], 'edges': []})
    assert created.status_code == 200
    wid = created.json()['id']
    prefix = '/api/workflows/' + wid
    assert client.post(prefix + '/publish').status_code == 200
    role['rotate'](role['new_password'])
    stale_test = client.post('/api/connections/' + connection['id'] + '/test')
    assert stale_test.status_code == 200 and stale_test.json()['ok'] is False
    assert 'authentication' in stale_test.json()['message']
    svc = client.app.state.workflows
    stale = client.post(prefix + '/run', json={'idempotency_key': 'stale-password'}).json()
    assert svc.execute_node(stale['id'], 'sql')['status'] == 'failed'
    assert svc.finish(stale['id'])['status'] == 'failed'
    rotated = client.put('/api/connections/' + connection['id'], json={'config': {'password': role['new_password']}, 'allowed_workflows': [wid]})
    assert rotated.status_code == 200 and rotated.json()['revision'] != connection['revision']
    assert client.post('/api/connections/' + connection['id'] + '/test').json()['ok'] is True
    admitted = client.post(prefix + '/run', json={'idempotency_key': 'authorized-new-password'}).json()
    state = svc.execute_node(admitted['id'], 'sql')
    assert state['status'] == 'succeeded', state
    assert state['credential_revision'] == rotated.json()['revision']
    assert int(state['output']['data']['rows'][0]['n']) == 2
    assert svc.finish(admitted['id'])['status'] == 'succeeded'
    documents = [client.get(prefix).text, client.get('/api/workflow-runs/' + stale['id']).text,
        client.get('/api/workflow-runs/' + admitted['id']).text, client.get(prefix + '/export').text,
        client.get('/api/connections').text, stale_test.text, rotated.text]
    assert all(role['old_password'] not in text and role['new_password'] not in text for text in documents)


@ENGINE
def test_sql15_sql16_actual_decimal_null_unicode_temporal_rows_reach_python_and_javascript(receiver, live):
    store, _, table, original = receiver
    dialect = original['dialect']
    db = connect(store, original, True)
    try:
        with db.cursor() as cur:
            if dialect == 'postgresql':
                cur.execute(f'ALTER TABLE {table} ADD COLUMN occurred_at TIMESTAMPTZ, ADD COLUMN calendar_day DATE')
                cur.execute(f"UPDATE {table} SET occurred_at=TIMESTAMPTZ '2026-03-08 01:30:00-06:00', calendar_day=DATE '2026-03-08' WHERE order_id='A001'")
                temporal = 'occurred_at AS "occurred_at", calendar_day AS "calendar_day"'
            elif dialect == 'mysql':
                cur.execute("SET time_zone='+00:00'")
                cur.execute(f'ALTER TABLE {table} ADD COLUMN occurred_at TIMESTAMP NULL, ADD COLUMN calendar_day DATE')
                cur.execute(f"UPDATE {table} SET occurred_at='2026-03-08 07:30:00', calendar_day='2026-03-08' WHERE order_id='A001'")
                temporal = 'DATE_FORMAT(occurred_at, \'%Y-%m-%dT%H:%i:%sZ\') AS "occurred_at", calendar_day AS "calendar_day"'
            else:
                cur.execute(f'ALTER TABLE {table} ADD (occurred_at TIMESTAMP WITH TIME ZONE, calendar_day DATE)')
                cur.execute(f"UPDATE {table} SET occurred_at=TO_TIMESTAMP_TZ('2026-03-08 01:30:00 -06:00','YYYY-MM-DD HH24:MI:SS TZH:TZM'), calendar_day=DATE '2026-03-08' WHERE order_id='A001'")
                temporal = 'TO_CHAR(occurred_at, \'YYYY-MM-DD"T"HH24:MI:SSTZH:TZM\') AS "occurred_at", TO_CHAR(calendar_day,\'YYYY-MM-DD\') AS "calendar_day"'
            cur.execute(sql_text(f'UPDATE {table} SET amount=:amount WHERE order_id=:id', dialect), {'amount': '9007199254740993.01', 'id': 'A001'})
            cur.execute(sql_text(f'INSERT INTO {table}(order_id,region) VALUES(:id,:region)', dialect), {'id': 'EMPTY_ROW', 'region': ''})
        db.commit()
        if dialect == 'oracle':
            wait_for_oracle_fixture_ddl(db, table)
    finally:
        db.close()
    config = fixture_config(dialect)
    if dialect == 'postgresql':
        config['options'] = '-c timezone=UTC'
    elif dialect == 'mysql':
        config['init_command'] = "SET time_zone='+00:00'"
    connection = clone_connection(live.store, receiver, config)
    source = f'SELECT order_id AS "order_id", amount AS "amount", big_value AS "big_value", region AS "region", {temporal} FROM {table} ORDER BY order_id'
    node = sql_node(connection['id'], dialect, source)
    nodes = [node]
    for language in ('python', 'javascript'):
        program = 'def main(inputs): return inputs["dataset"]' if language == 'python' else 'function main(inputs){return inputs.dataset;}'
        nodes.append({'id': language, 'kind': language, 'source': program, 'config': {},
            'inputs': {'dataset': {'source': 'node', 'node_id': 'sql', 'path': []}}})
    wf = live.save({'name': 'Exact external typed rows', 'nodes': nodes,
        'edges': [{'source': 'sql', 'target': lang} for lang in ('python', 'javascript')]})
    live.publish(wf['id'])
    run = live.admit(wf['id'])
    execute_graph(live, run['id'])
    result = live.get_run(run['id'])
    assert result['status'] == 'succeeded', result.get('error')
    data = result['nodes']['sql']['output']['data']
    first, second, empty = data['rows']
    assert data['rowCount'] == 3
    assert second['order_id'] == 'A002' and empty['order_id'] == 'EMPTY_ROW'
    assert empty['region'] == (None if dialect == 'oracle' else '')
    assert isinstance(first['amount'], str) and Decimal(first['amount']) == Decimal('9007199254740993.01')
    assert first['big_value'] == '9007199254740993'
    assert first['region'] == '华东 🚀' and second['region'] is None
    assert first['calendar_day'] == '2026-03-08'
    assert datetime.fromisoformat(first['occurred_at'].replace('Z', '+00:00')).astimezone(timezone.utc).isoformat() == '2026-03-08T07:30:00+00:00'
    assert second['calendar_day'] is None and second['occurred_at'] is None
    types = {column['name']: column['type'] for column in data['columns']}
    assert all(types[key] == 'string' for key in ('amount', 'big_value', 'calendar_day', 'occurred_at'))
    for language in ('python', 'javascript'):
        assert result['nodes'][language]['output']['data'] == data
        assert len(result['nodes'][language]['attempts']) == 1
    assert result['n8n_execution_id'] and result['graph_execution_id']


@pytest.mark.parametrize('commit_ack_lost', [False, True])
def test_local_sql_commit_teardown_failure_preserves_business_outcome(tmp_path, monkeypatch, commit_ack_lost):
    """A failed close must neither invite replay nor hide a commit-uncertain result."""
    from taskconsole.store import Store
    import taskconsole.workflows_sql as sql
    store = Store(tmp_path, 'sqlite:///' + str(tmp_path / 'state.sqlite'))
    connection = create_connection(store, {'name': 'Commit teardown oracle', 'dialect': 'sqlite', 'config': {}, 'write_enabled': True})
    path = store.path / 'workflow-connections' / (connection['id'] + '.sqlite')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE effects(order_id TEXT PRIMARY KEY)')
    original = sql.connect
    def connect_with_failed_close(*args, **kwargs):
        db = original(*args, **kwargs)
        class Connection:
            def __getattr__(self, key):
                return getattr(db, key)
            def commit(self):
                db.commit()
                if commit_ack_lost:
                    raise ConnectionResetError('Synthetic lost commit acknowledgement')
            def close(self):
                db.close()
                raise ConnectionResetError('Synthetic teardown transport error')
        return Connection()
    monkeypatch.setattr(sql, 'connect', connect_with_failed_close)
    try:
        result = execute_sql(store, sql_node(connection['id'], 'sqlite', "INSERT INTO effects VALUES('effect-001')", 'write'), {}, 'teardown')
        assert result['status'] == ('failed' if commit_ack_lost else 'succeeded')
        if commit_ack_lost:
            assert result['retryable'] is False and result['reason'] == 'commit_outcome_uncertain'
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT COUNT(*) FROM effects WHERE order_id='effect-001'").fetchone()[0] == 1
    finally:
        store.engine.dispose()
