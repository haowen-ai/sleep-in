"""SQLite acceptance with actual connections, independent reads and real n8n."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import sysconfig
import time

import pytest
from test_acceptance_data import live, configured_node, ORDERS
from taskconsole.workflows_sql import create_connection, execute_sql


@pytest.fixture
def database(live):
    connection = create_connection(live.store, {'name': 'SQLite boundary fixture', 'dialect': 'sqlite',
        'config': {'synthetic': True}, 'write_enabled': True})
    path = live.store.path / 'workflow-connections' / (connection['id'] + '.sqlite')
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE effects(effect_id TEXT PRIMARY KEY, value TEXT NOT NULL)')
        db.execute('CREATE TABLE mutation_log(operation TEXT)')
        for table in ('orders', 'effects'):
            for operation in ('INSERT', 'UPDATE', 'DELETE'):
                db.execute(f'CREATE TRIGGER observe_{table}_{operation} AFTER {operation} ON {table} '
                    f"BEGIN INSERT INTO mutation_log VALUES ('{table}:{operation}'); END")
    return live, connection, path


def sql(connection, source, nid='query', **config):
    return {'id': nid, 'kind': 'sql', 'source': source, 'inputs': {},
        'config': {'dialect': 'sqlite', 'connection_id': connection['id'], **config}}


def rows(path, query):
    with sqlite3.connect('file:' + str(path) + '?mode=ro', uri=True) as db:
        return db.execute(query).fetchall()


def orders(path):
    return [dict(zip(('order_id', 'amount', 'region'), row)) for row in rows(path, 'SELECT * FROM orders ORDER BY order_id')]


def published(svc, nodes, edges=None):
    wf = svc.save({'name': 'SQLite acceptance', 'nodes': nodes, 'edges': edges or []})
    svc.publish(wf['id'])
    return wf


def native(svc, wf, key=None):
    from taskconsole.workflows_n8n import execute_graph
    run = svc.admit(wf['id'], key=key); execute_graph(svc, run['id']); run = svc.get_run(run['id'])
    assert run.get('n8n_execution_id') and run.get('graph_execution_id'), run.get('adapter_log')
    return run


def test_sql03_injection_value_does_not_change_template_or_full_orders_table(database):
    svc, connection, path = database
    node = sql(connection, 'SELECT * FROM orders WHERE order_id=:order_id')
    before = orders(path); assert before == ORDERS
    result = execute_sql(svc.store, node, {'order_id': "A001' OR 1=1 --"}, 'injection-fixture')
    assert result['output']['data']['rows'] == [] and result['output']['data']['rowCount'] == 0
    assert execute_sql(svc.store, node, {'order_id': 'A001'}, 'injection-fixture')['output']['data']['rows'] == [ORDERS[0]]
    assert orders(path) == before and rows(path, 'SELECT * FROM mutation_log') == []


@pytest.mark.parametrize('source', [
    "INSERT INTO effects VALUES('effect-001','forbidden')",
    "UPDATE orders SET amount='999' WHERE order_id='A001'",
    'DELETE FROM orders',
    "/* misleading SELECT */ INSERT INTO effects VALUES('effect-001','forbidden')",
    "-- SELECT ignored\nUPDATE orders SET amount='999'",
    "WITH data(x) AS (SELECT 'effect-001') INSERT INTO effects SELECT x,'forbidden' FROM data",
    "WITH chosen(id) AS (SELECT 'A001') UPDATE orders SET amount='999' WHERE order_id IN chosen",
    "WITH chosen(id) AS (SELECT 'A001') DELETE FROM orders WHERE order_id IN chosen",
])
def test_sql07_query_connection_rejects_writes_independent_of_first_token(database, source):
    svc, connection, path = database
    with pytest.raises(sqlite3.DatabaseError, match='readonly|read-only'):
        execute_sql(svc.store, sql(connection, source), {}, 'readonly-fixture')
    assert orders(path) == ORDERS
    assert rows(path, 'SELECT * FROM effects') == rows(path, 'SELECT * FROM mutation_log') == []


@pytest.mark.parametrize('operation', ['attach', 'vacuum_into', 'journal_mode', 'user_version', 'load_extension'])
def test_sql08_sqlite_escape_attempts_cannot_open_or_modify_host_canary(database, operation):
    svc, connection, path = database
    canary = svc.store.path / 'outside-managed-storage.txt'; canary.write_bytes(b'HOST-CANARY-UNCHANGED')
    new_file = svc.store.path / 'forbidden-external.sqlite'
    escaped = str(canary).replace("'", "''")
    source = {'attach': f"ATTACH DATABASE '{escaped}' AS outside",
        'vacuum_into': f"VACUUM INTO '{new_file}'", 'journal_mode': 'PRAGMA journal_mode=WAL',
        'user_version': 'PRAGMA user_version=123', 'load_extension': f"SELECT load_extension('{escaped}')"}[operation]
    with pytest.raises(sqlite3.DatabaseError) as rejected:
        execute_sql(svc.store, sql(connection, source), {}, 'escape-fixture')
    if operation in {'attach', 'load_extension'}:
        assert 'not authorized' in str(rejected.value).lower() or 'no such function' in str(rejected.value).lower()
    assert canary.read_bytes() == b'HOST-CANARY-UNCHANGED' and not new_file.exists()
    assert orders(path) == ORDERS and rows(path, 'SELECT * FROM mutation_log') == []
    # Read-only introspection is an explicitly supported PRAGMA.
    result = execute_sql(svc.store, sql(connection, 'PRAGMA table_info(orders)'), {}, 'escape-fixture')
    assert [r['name'] for r in result['output']['data']['rows']] == ['order_id', 'amount', 'region']


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'), reason='BLOCKED_ENV: actual n8n required')
def test_sql09_n8n_explicit_write_commits_effect001_once(database):
    svc, connection, path = database
    node = sql(connection, 'INSERT INTO effects VALUES(:effect,:value)', mode='write')
    node['inputs'] = {name: {'source': 'constant', 'value': value} for name, value in {'effect': 'effect-001', 'value': 'committed'}.items()}
    wf = published(svc, [node]); run = native(svc, wf, 'once-only-write')
    assert run['status'] == 'succeeded' and run['nodes']['query']['output']['data']['affectedRows'] == 1
    assert svc.admit(wf['id'], key='once-only-write')['id'] == run['id']
    assert rows(path, 'SELECT * FROM effects') == [('effect-001', 'committed')]
    assert rows(path, 'SELECT * FROM mutation_log') == [('effects:INSERT',)] and orders(path) == ORDERS


def test_sql10_parameterized_statements_list_rolls_back_insert_on_later_failure(database):
    svc, connection, path = database
    node = sql(connection, 'SELECT 1', mode='write', statements=[
        {'sql': 'INSERT INTO effects VALUES(:id,:value)', 'bindings': {'id': {'input': 'effect'}, 'value': 'a;b'}},
        {'sql': 'INSERT INTO nonexistent_table VALUES(:id)', 'bindings': {'id': {'input': 'effect'}}}])
    node['inputs'] = {'effect': {'source': 'constant', 'value': 'effect-001'}}
    wf = published(svc, [node]); run = svc.admit(wf['id'])
    state = svc.execute_node(run['id'], 'query')
    assert state['status'] == 'failed' and 'nonexistent_table' in state['error']
    assert svc.finish(run['id'])['status'] == 'failed'
    assert rows(path, 'SELECT * FROM effects') == rows(path, 'SELECT * FROM mutation_log') == []
    assert orders(path) == ORDERS


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'), reason='BLOCKED_ENV: actual n8n required')
def test_sql11_n8n_node_a_commit_survives_node_b_transaction_rollback(database):
    svc, connection, path = database
    a = sql(connection, "INSERT INTO effects VALUES('effect-001','A committed')", 'a', mode='write')
    b = sql(connection, 'SELECT 1', 'b', mode='write', statements=[
        {'sql': 'INSERT INTO effects VALUES(:id,:value)', 'bindings': {'id': 'effect-002', 'value': 'B rollback'}},
        {'sql': 'INSERT INTO missing_table VALUES(1)'}])
    wf = published(svc, [a, b], [{'source': 'a', 'target': 'b'}]); run = native(svc, wf)
    assert run['status'] == 'failed'
    assert run['nodes']['a']['status'] == 'succeeded' and run['nodes']['a']['output']['data']['affectedRows'] == 1
    assert run['nodes']['b']['status'] == 'failed'
    assert rows(path, 'SELECT * FROM effects') == [('effect-001', 'A committed')]
    assert rows(path, 'SELECT * FROM mutation_log') == [('effects:INSERT',)]


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'), reason='BLOCKED_ENV: actual n8n required')
def test_sql13_n8n_sqlite_timeout_rolls_back_and_blocks_downstream(database):
    svc, connection, path = database
    slow = 'WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n'
    a = sql(connection, slow, 'slow', mode='write', timeout=1, statements=[
        {'sql': "INSERT INTO effects VALUES('effect-001','must roll back')"}, {'sql': slow}])
    b = sql(connection, "INSERT INTO effects VALUES('effect-002','forbidden downstream')", 'never', mode='write')
    wf = published(svc, [a, b], [{'source': 'slow', 'target': 'never'}]); run = native(svc, wf)
    assert run['status'] == 'timed_out' and run['nodes']['slow']['status'] == 'timed_out'
    assert 'timeout' in run['nodes']['slow']['error'].lower() or 'deadline' in run['nodes']['slow']['error'].lower()
    assert run['nodes']['never']['status'] == 'not_run' and run['nodes']['never']['attempts'] == []
    assert rows(path, 'SELECT * FROM effects') == rows(path, 'SELECT * FROM mutation_log') == []
    assert execute_sql(svc.store, sql(connection, 'SELECT count(*) AS count FROM orders'), {}, wf['id'])['output']['data']['rows'] == [{'count': 3}]


def test_sqlite_explicit_cancellation_rolls_back_interrupted_transaction(database):
    svc, connection, path = database
    slow = 'WITH RECURSIVE n(x) AS (VALUES(0) UNION ALL SELECT x+1 FROM n WHERE x<1000000000) SELECT sum(x) FROM n'
    node = sql(connection, slow, mode='write', timeout=30, statements=[
        {'sql': "INSERT INTO effects VALUES('effect-001','must roll back')"}, {'sql': slow}])
    cancellation_at = time.monotonic() + .1
    result = execute_sql(svc.store, node, {}, 'cancel-fixture', lambda: time.monotonic() >= cancellation_at)
    assert result['status'] == 'cancelled' and 'cancelled' in result['error']
    assert rows(path, 'SELECT * FROM effects') == rows(path, 'SELECT * FROM mutation_log') == []
    assert orders(path) == ORDERS


def test_sql18_unauthorized_workflow_rejected_before_database_connect(database, monkeypatch):
    from taskconsole import workflows_sql
    svc, connection, path = database; client = svc.acceptance_client
    a = published(svc, [sql(connection, 'SELECT 1')])
    assert client.put('/api/connections/' + connection['id'], json={'allowed_workflows': [a['id']]}).status_code == 200
    called = []; original = workflows_sql.connect
    def spy(*args, **kwargs):
        called.append(args[1]['id']); return original(*args, **kwargs)
    monkeypatch.setattr(workflows_sql, 'connect', spy)
    b = client.post('/api/workflows', json={'name': 'Forbidden B', 'nodes': [sql(connection, 'SELECT 1')], 'edges': []})
    assert b.status_code == 200
    denied = client.post('/api/workflows/' + b.json()['id'] + '/publish')
    assert denied.status_code == 422 and 'authorized' in denied.text.lower()
    admitted = svc.admit(a['id'])
    assert client.put('/api/connections/' + connection['id'], json={'allowed_workflows': [b.json()['id']]}).status_code == 200
    state = svc.execute_node(admitted['id'], 'query')
    assert state['status'] == 'failed' and 'authorized' in state['error'].lower()
    assert called == [] and orders(path) == ORDERS


@pytest.mark.parametrize('key', ['path', 'file', 'database', 'url'])
@pytest.mark.parametrize('value_kind', ['absolute', 'traversal'])
def test_sql24_api_rejects_unmanaged_paths_before_opening_host_database(database, monkeypatch, key, value_kind):
    svc, connection, _ = database; client = svc.acceptance_client
    outside = svc.store.path / 'user-canary.db'; outside.write_bytes(b'NOT-A-USER-DATABASE-OPEN-ME')
    value = str(outside) if value_kind == 'absolute' else '../../user-canary.db'
    calls = []; original = sqlite3.connect
    def spy(target, *args, **kwargs):
        if 'user-canary.db' in str(target): calls.append(str(target))
        return original(target, *args, **kwargs)
    monkeypatch.setattr(sqlite3, 'connect', spy)
    before = client.get('/api/connections').json()
    created = client.post('/api/connections', json={'name': 'Host escape', 'dialect': 'sqlite', 'config': {key: value}})
    changed = client.put('/api/connections/' + connection['id'], json={'config': {key: value}})
    assert created.status_code == changed.status_code == 400
    assert 'managed storage' in created.text and 'managed storage' in changed.text
    assert client.get('/api/connections').json() == before
    assert calls == [] and outside.read_bytes() == b'NOT-A-USER-DATABASE-OPEN-ME'


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'), reason='BLOCKED_ENV: actual n8n required')
def test_sql14_twenty_thousand_rows_reach_python_with_checksum_and_paginated_preview(database):
    svc, connection, path = database
    expected = [{'id': i, 'value': f'row-{i:05d}:' + 'z' * 80} for i in range(20000)]
    checksum = hashlib.sha256(json.dumps(expected, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE big_data(id INTEGER PRIMARY KEY,value TEXT)')
        db.executemany('INSERT INTO big_data VALUES(:id,:value)', expected)
    read = sql(connection, 'SELECT id,value FROM big_data ORDER BY id')
    echo = {'id': 'python', 'kind': 'python', 'config': {},
        'source': 'import json,hashlib\ndef main(inputs):\n rows=inputs["rows"]\n return {"count":len(rows),"sha256":hashlib.sha256(json.dumps(rows,ensure_ascii=False,separators=(",",":")).encode()).hexdigest()}',
        'inputs': {'rows': {'source': 'node', 'node_id': 'query', 'path': 'rows'}}}
    wf = published(svc, [read, echo], [{'source': 'query', 'target': 'python'}]); run = native(svc, wf)
    assert run['status'] == 'succeeded'
    assert run['nodes']['python']['output']['data'] == {'count': 20000, 'sha256': checksum}
    assert run['nodes']['query']['output']['data_ref']['size'] > 1024 * 1024
    endpoint = f'/api/workflow-runs/{run["id"]}/nodes/query/output'
    for offset in (0, 100, 19900):
        response = svc.acceptance_client.get(endpoint, params={'path': 'rows', 'offset': offset, 'limit': 100})
        assert response.status_code == 200
        page = response.json()
        assert page['data'] == expected[offset:offset + 100] and page['total'] == 20000
        assert page['offset'] == offset and page['limit'] == 100
        assert page['has_more'] is (offset + 100 < 20000)
    item = next(a for a in run['artifacts'] if a['node_id'] == 'query')
    downloaded = svc.acceptance_client.get(f'/api/workflow-runs/{run["id"]}/artifacts/{item["id"]}')
    assert downloaded.status_code == 200 and hashlib.sha256(downloaded.content).hexdigest() == item['sha256']
    assert json.loads(downloaded.content)['rows'] == expected


def test_data16_sqlite_null_and_empty_string_stay_distinct_through_real_python(database):
    svc, connection, path = database
    with sqlite3.connect(path) as db:
        db.execute('CREATE TABLE nullable_text(id INTEGER PRIMARY KEY,value TEXT)')
        db.executemany('INSERT INTO nullable_text VALUES(?,?)', [(1, None), (2, '')])
    read = sql(connection, 'SELECT id,value FROM nullable_text ORDER BY id')
    echo = {'id': 'python', 'kind': 'python', 'config': {}, 'source': 'def main(inputs): return inputs',
        'inputs': {'rows': {'source': 'node', 'node_id': 'query', 'path': 'rows'}}}
    wf = published(svc, [read, echo], [{'source': 'query', 'target': 'python'}]); run = svc.admit(wf['id'])
    assert svc.execute_node(run['id'], 'query')['status'] == 'succeeded'
    result = svc.execute_node(run['id'], 'python')
    assert result['status'] == 'succeeded'
    assert result['output']['data']['rows'] == [{'id': 1, 'value': None}, {'id': 2, 'value': ''}]
    assert svc.finish(run['id'])['status'] == 'succeeded'


def test_sql19_actual_absent_optional_drivers_are_unavailable_and_block_publication(tmp_path):
    # Actual isolated import environment: all installed application dependencies except the
    # three optional drivers. No import hook, detector monkeypatch or replacement interpreter.
    dependencies = tmp_path / 'application-dependencies'; dependencies.mkdir()
    for package in Path(sysconfig.get_path('purelib')).iterdir():
        if package.name.startswith(('psycopg', 'pymysql', 'oracledb')):
            continue
        (dependencies / package.name).symlink_to(package, target_is_directory=package.is_dir())
    repo = Path(__file__).resolve().parents[1]
    script = '''
import sys,json,importlib.util
sys.path[:0]=[sys.argv[1],sys.argv[2]]
from pathlib import Path
from fastapi.testclient import TestClient
from taskconsole.app import create_app
for module in ('psycopg','pymysql','oracledb'):
    assert importlib.util.find_spec(module) is None,module
state=Path(sys.argv[3]);app=create_app(state,'sqlite:///'+str(state/'console.sqlite'))
with TestClient(app) as client:
    setup=client.post('/api/setup',json={'token':(state/'setup-token').read_text().strip(),'username':'owner','password':'isolated driver fixture','timezone':'UTC','locale':'en'})
    assert setup.status_code==200,setup.text
    client.headers['X-CSRF-Token']=setup.json()['csrf']
    for dialect in ('postgresql','mysql','oracle'):
        response=client.post('/api/connections',json={'name':dialect,'dialect':dialect,'config':{'host':'127.0.0.1','port':1}})
        assert response.status_code==200,response.text
        connection=response.json();assert connection['driver_available'] is False
        graph={'name':dialect,'nodes':[{'id':'sql','kind':'sql','source':'SELECT 1','inputs':{},'config':{'dialect':dialect,'connection_id':connection['id']}}],'edges':[]}
        saved=client.post('/api/workflows',json=graph);assert saved.status_code==200,saved.text
        denied=client.post('/api/workflows/'+saved.json()['id']+'/publish')
        assert denied.status_code==422 and dialect in denied.text and 'driver' in denied.text,denied.text
        tested=client.post('/api/connections/'+connection['id']+'/test')
        assert tested.status_code==200 and tested.json()['ok'] is False,tested.text
    assert client.get('/api/workflow-runs').json()==[]
app.state.store.engine.dispose()
print('three actual absent drivers blocked')
'''
    env = {key: value for key, value in os.environ.items() if key not in {'SLEEP_IN_LOCAL', 'PYTHONPATH'}}
    result = subprocess.run([sys.executable, '-I', '-S', '-c', script, str(repo), str(dependencies), str(tmp_path / 'state')],
        capture_output=True, text=True, env=env, timeout=45)
    assert result.returncode == 0, result.stdout + result.stderr
    assert 'three actual absent drivers blocked' in result.stdout
