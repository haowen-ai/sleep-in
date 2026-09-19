"""Real disposable Oracle Thin and PostgreSQL TLS boundaries; never mocked D evidence.

External database opt-ins are mandatory. Version/platform properties describe only
the actual runner and pinned image; they do not claim wallets, Thick mode or macOS.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import platform
import re
import subprocess
import time
import uuid

import pytest

from test_workflow_api_contract import client, admin
from test_workflow_external_sql import fixture_config, wait_for_oracle_fixture_ddl
from test_workflow_execution_n8n_complete import live
from taskconsole.workflows import public_run
from taskconsole.workflows_n8n import execute_graph
from taskconsole.workflows_sql import create_connection, connect, execute_sql


ORDERS = [{'order_id': 'A001', 'amount': '10.50', 'region': '华东'},
          {'order_id': 'A002', 'amount': '20.25', 'region': None},
          {'order_id': 'A003', 'amount': '0.00', 'region': '西部'}]
ROOT = Path(__file__).resolve().parents[1]


def image_for(service):
    compose = (ROOT / 'deploy/sql-matrix-compose.yaml').read_text()
    return re.search(r'^  ' + service + r':\n    image: (\S+)', compose, re.M).group(1)


def stored(store, cid):
    with store.transaction() as tx:
        return tx.get('connection', cid)


def node_for(cid, source, mode='query', statements=None):
    node = {'id': 'sql', 'kind': 'sql', 'source': source or (statements[0]['sql'] if statements else ''), 'inputs': {},
            'config': {'connection_id': cid, 'dialect': 'oracle', 'mode': mode, 'timeout': 15}}
    if statements is not None:
        node['config']['statements'] = statements
    return node


@pytest.fixture
def oracle_orders(client, record_property):
    if os.environ.get('SLEEP_IN_EXTERNAL_SQL_TESTS') != '1':
        pytest.skip('BLOCKED_ENV: actual disposable external Oracle fixture required')
    if 'oracle' not in os.environ.get('SLEEP_IN_TEST_SQL_DIALECTS', 'postgresql,mysql,oracle').split(','):
        pytest.skip('Oracle excluded by fixture selection')
    import oracledb
    admin(client)
    response = client.post('/api/connections', json={'name': 'Exact Oracle Thin F-ORDERS',
        'dialect': 'oracle', 'config': fixture_config('oracle'), 'write_enabled': True})
    assert response.status_code == 200
    store = client.app.state.store
    connection = stored(store, response.json()['id'])
    db = connect(store, connection, True)
    table = 'si_orders_' + uuid.uuid4().hex[:12]
    created = False
    try:
        assert oracledb.is_thin_mode() is True
        for key, value in {'driver_version': oracledb.__version__, 'server_version': db.version,
            'driver_mode': 'Thin', 'platform': platform.platform(), 'architecture': platform.machine(),
            'fixture_image': image_for('oracle'), 'fixture_scope': 'disposable Oracle Free container; actual runner only; no wallet or Thick claim',
            'column_types': 'order_id VARCHAR2(30 CHAR), amount VARCHAR2(30 CHAR), region VARCHAR2(100 CHAR)'}.items():
            record_property(key, value)
        with db.cursor() as cur:
            cur.execute(f'CREATE TABLE {table} (order_id VARCHAR2(30 CHAR) PRIMARY KEY, amount VARCHAR2(30 CHAR) NOT NULL, region VARCHAR2(100 CHAR))')
            created = True
            cur.executemany(f'INSERT INTO {table} VALUES(:order_id,:amount,:region)', ORDERS)
        db.commit()
        wait_for_oracle_fixture_ddl(db, table)
        yield store, connection, table, db
    finally:
        try:
            db.rollback()
            if created:
                with db.cursor() as cur:
                    cur.execute('DROP TABLE ' + table + ' PURGE')
        finally:
            db.close()


def query(store, cid, table, where='', inputs=None):
    source = f'SELECT order_id AS "order_id", amount AS "amount", region AS "region" FROM {table}' + where
    return execute_sql(store, node_for(cid, source), inputs or {}, 'oracle-exact')['output']['data']


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'), reason='BLOCKED_ENV: actual n8n required for SQL09-OR')
def test_sql22_exact_oracle_thin_query_bind_null_commit_and_rollback(oracle_orders, live, record_property):
    store, connection, table, independent = oracle_orders
    cid = connection['id']
    full = query(store, cid, table, ' ORDER BY order_id')
    assert full == {'rows': ORDERS, 'columns': [
        {'name': 'order_id', 'type': 'string', 'nullable': False},
        {'name': 'amount', 'type': 'string', 'nullable': False},
        {'name': 'region', 'type': 'string', 'nullable': True}], 'rowCount': 3}
    assert query(store, cid, table, ' WHERE order_id=:order_id', {'order_id': 'A002'})['rows'] == [ORDERS[1]]
    assert query(store, cid, table, ' WHERE region IS NULL AND :nullable IS NULL', {'nullable': None})['rows'] == [ORDERS[1]]
    assert query(store, cid, table, ' WHERE order_id=:order_id', {'order_id': "A001' OR 1=1 --"})['rows'] == []
    assert query(store, cid, table, ' ORDER BY order_id') == full
    # Real graph-engine admission and explicit SQL write commit exactly once.
    real_connection = create_connection(live.store, {'name': 'Oracle effect ledger', 'dialect': 'oracle',
        'config': fixture_config('oracle'), 'write_enabled': True})
    effect = node_for(real_connection['id'], f"INSERT INTO {table} VALUES ('effect-001','1.00','committed')", 'write')
    wf = live.save({'name': 'Exact Oracle SQL09', 'nodes': [effect], 'edges': []})
    live.publish(wf['id']); admitted = live.admit(wf['id'], key='oracle-effect-once')
    execute_graph(live, admitted['id'])
    result = live.get_run(admitted['id'])
    assert result['status'] == 'succeeded' and result['n8n_execution_id']
    assert len(result['nodes']['sql']['attempts']) == 1
    assert result['nodes']['sql']['output']['data']['affectedRows'] == 1
    assert live.admit(wf['id'], key='oracle-effect-once')['id'] == admitted['id']
    execute_graph(live, admitted['id'])
    with independent.cursor() as cur:
        cur.execute(f"SELECT COUNT(*) FROM {table} WHERE order_id='effect-001'")
        assert int(cur.fetchone()[0]) == 1
        # Reset only the owned effect fixture so SQL10 uses the exact same ID.
        cur.execute(f"DELETE FROM {table} WHERE order_id='effect-001'")
    independent.commit()
    missing = 'si_absent_' + uuid.uuid4().hex[:12]
    for first in [
        {'sql': f'INSERT INTO {table} VALUES(:id,:amount,:region)', 'bindings': {'id': 'effect-001', 'amount': '1.00', 'region': 'rolled-back'}},
        {'sql': f'UPDATE {table} SET region=:region WHERE order_id=:id', 'bindings': {'region': 'rolled-back', 'id': 'A001'}},
    ]:
        failing = node_for(cid, '', 'write', [first, {'sql': 'INSERT INTO ' + missing + ' VALUES (1)', 'bindings': {}}])
        with pytest.raises(Exception) as failure:
            execute_sql(store, failing, {}, 'oracle-rollback')
        assert failure.value.args[0].code == 942
        assert query(store, cid, table, ' ORDER BY order_id') == full
    updated = execute_sql(store, node_for(cid, f'UPDATE {table} SET region=:region WHERE order_id=:id', 'write'),
                          {'region': 'committed-update', 'id': 'A001'}, 'oracle-update')
    assert updated['output']['data']['affectedRows'] == 1
    with independent.cursor() as cur:
        cur.execute(f"SELECT region FROM {table} WHERE order_id='A001'")
        assert cur.fetchone()[0] == 'committed-update'
    record_property('actual_results', 'F-ORDERS exact; A002 bind and null; injection empty; effect-001 committed once via n8n; failing insert/update both rolled back; update committed')


def test_sql23_oracle_ddl_implicit_commit_survives_later_failure_without_false_rollback_claim(oracle_orders, client, record_property):
    store, connection, table, independent = oracle_orders
    extra = 'si_ddl_' + uuid.uuid4().hex[:12]
    missing = 'si_absent_' + uuid.uuid4().hex[:12]
    statements = [
        {'sql': f'UPDATE {table} SET region=:region WHERE order_id=:id', 'bindings': {'region': 'committed-before-ddl', 'id': 'A001'}},
        {'sql': f'CREATE TABLE {extra} (id NUMBER PRIMARY KEY)', 'bindings': {}},
        {'sql': f'INSERT INTO {extra}(id) VALUES(:id)', 'bindings': {'id': 1}},
        {'sql': f'INSERT INTO {missing} VALUES(1)', 'bindings': {}},
    ]
    try:
        svc = client.app.state.workflows
        node = node_for(connection['id'], '', 'write', statements)
        wf = svc.save({'name': 'Oracle implicit DDL commit', 'nodes': [node], 'edges': []})
        svc.publish(wf['id']); run = svc.admit(wf['id'])
        state = svc.execute_node(run['id'], 'sql')
        final = public_run(svc.finish(run['id']))
        assert state['status'] == final['status'] == 'failed'
        assert len(state['attempts']) == 1
        assert 'ORA-00942' in state['error']
        assert 'rolled back' not in state['error'].lower() and 'rollback' not in state['error'].lower()
        assert final['side_effects_uncertain'] is True
        assert final['review_reason']['code'] == 'check_external_effects'
        with independent.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM USER_TABLES WHERE TABLE_NAME=:name', {'name': extra.upper()})
            assert int(cur.fetchone()[0]) == 1
            cur.execute(f'SELECT COUNT(*) FROM {extra}')
            assert int(cur.fetchone()[0]) == 0
            cur.execute(f"SELECT region FROM {table} WHERE order_id='A001'")
            assert cur.fetchone()[0] == 'committed-before-ddl'
        record_property('implicit_commit_outcome', 'Oracle CREATE TABLE committed prior UPDATE and table creation; later INSERT rolled back after ORA-00942; failed run retains explicit external-effect review warning')
    finally:
        independent.rollback()
        with independent.cursor() as cur:
            cur.execute('SELECT COUNT(*) FROM USER_TABLES WHERE TABLE_NAME=:name', {'name': extra.upper()})
            if int(cur.fetchone()[0]):
                cur.execute('DROP TABLE ' + extra + ' PURGE')


@pytest.fixture
def tls_postgres(tmp_path, record_property):
    if os.environ.get('SLEEP_IN_EXTERNAL_TLS_TESTS') != '1':
        pytest.skip('BLOCKED_ENV: disposable Docker PostgreSQL TLS opt-in required')
    import psycopg
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID
    certs = tmp_path / 'tls-fixture'; certs.mkdir()
    current = datetime.now(timezone.utc)
    def authority(label):
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, label)])
        certificate = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(current - timedelta(minutes=5)).not_valid_after(current + timedelta(days=1))
            .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True).sign(key, hashes.SHA256()))
        return key, certificate
    ca_key, ca = authority('Sleep In disposable CA')
    _, wrong_ca = authority('Sleep In unrelated CA')
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    hostname = 'expected.sleepin.invalid'
    server = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, hostname)]))
        .issuer_name(ca.subject).public_key(server_key.public_key()).serial_number(x509.random_serial_number())
        .not_valid_before(current - timedelta(minutes=5)).not_valid_after(current + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(hostname)]), critical=False)
        .sign(ca_key, hashes.SHA256()))
    for name, value in [('ca.crt', ca), ('wrong-ca.crt', wrong_ca), ('server.crt', server)]:
        (certs / name).write_bytes(value.public_bytes(serialization.Encoding.PEM))
    (certs / 'server.key').write_bytes(server_key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))
    (certs / 'server.key').chmod(0o600)
    container = 'si-tls-' + uuid.uuid4().hex[:12]
    image = image_for('postgres')
    started = attempted = False
    try:
        command = ['docker', 'run', '--detach', '--rm', '--name', container,
            '--publish', '127.0.0.1::5432', '--mount', 'type=bind,src=' + str(certs) + ',dst=/fixtures,readonly',
            '--env', 'POSTGRES_USER=sleepin', '--env', 'POSTGRES_PASSWORD=sleepin_fixture_password', '--env', 'POSTGRES_DB=sleepin',
            image, 'sh', '-c', 'cp /fixtures/server.key /tmp/server.key && cp /fixtures/server.crt /tmp/server.crt && '
            'chown postgres:postgres /tmp/server.key /tmp/server.crt && chmod 600 /tmp/server.key && '
            'exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/server.crt -c ssl_key_file=/tmp/server.key']
        attempted = True
        result = subprocess.run(command, capture_output=True, text=True, timeout=90)
        assert result.returncode == 0, result.stderr
        started = True
        port_result = subprocess.run(['docker', 'port', container, '5432/tcp'], capture_output=True, text=True, timeout=10)
        assert port_result.returncode == 0, port_result.stderr
        address = port_result.stdout.strip()
        assert address.startswith('127.0.0.1:')
        config = {'host': hostname, 'hostaddr': '127.0.0.1', 'port': int(address.rsplit(':', 1)[1]),
            'dbname': 'sleepin', 'user': 'sleepin', 'password': 'sleepin_fixture_password',
            'connect_timeout': 3, 'sslmode': 'verify-full', 'sslrootcert': str(certs / 'ca.crt')}
        deadline = time.monotonic() + 60
        while True:
            try:
                with psycopg.connect(**config) as db:
                    with db.cursor() as cur:
                        cur.execute('SELECT ssl,version FROM pg_stat_ssl WHERE pid=pg_backend_pid()')
                        ssl, version = cur.fetchone()
                        assert ssl is True and version.startswith('TLS')
                    record_property('server_version', db.info.server_version)
                break
            except psycopg.OperationalError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(.25)
        record_property('fixture_image', image)
        record_property('driver_version', psycopg.__version__)
        record_property('tls_version', version)
        record_property('platform', platform.platform())
        yield config, certs
    finally:
        if attempted:
            result = subprocess.run(['docker', 'rm', '--force', container], capture_output=True, text=True, timeout=30)
            assert result.returncode == 0 or (not started and 'No such container' in result.stderr), result.stderr


@pytest.mark.parametrize('fault', ['untrusted_ca', 'wrong_hostname'])
def test_sql20_actual_tls_verification_failure_never_downgrades_connection(client, tls_postgres, fault, record_property):
    import psycopg
    config, certs = tls_postgres
    admin(client)
    def api_test(settings):
        response = client.post('/api/connections', json={'name': 'TLS fixture ' + fault,
            'dialect': 'postgresql', 'config': settings, 'write_enabled': False})
        assert response.status_code == 200
        cid = response.json()['id']
        response = client.post('/api/connections/' + cid + '/test')
        assert response.status_code == 200
        return response
    assert api_test(config).json()['ok'] is True
    plaintext = {**config, 'sslmode': 'disable'}
    with psycopg.connect(**plaintext) as db:
        with db.cursor() as cur:
            cur.execute('SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()')
            assert cur.fetchone()[0] is False
    assert api_test(plaintext).json()['ok'] is True
    invalid = {**config, **({'sslrootcert': str(certs / 'wrong-ca.crt')} if fault == 'untrusted_ca'
                          else {'host': 'wrong.sleepin.invalid'})}
    with pytest.raises(psycopg.OperationalError) as rejected:
        psycopg.connect(**invalid)
    message = str(rejected.value).lower()
    assert ('certificate verify failed' in message if fault == 'untrusted_ca' else 'does not match host name' in message)
    failed = api_test(invalid)
    assert failed.json()['ok'] is False
    assert 'connection test failed' in failed.json()['message'].lower()
    assert config['password'] not in failed.text
    assert api_test(config).json()['ok'] is True
    record_property('verification_fault', fault)
    record_property('downgrade_control', 'same server accepts explicit plaintext, but invalid verified TLS returns failure through product API')
