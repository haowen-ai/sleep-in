"""CAN04: real Oracle commit followed by outstanding server work, then cancellation."""
import os
import platform
import threading
import time
import uuid

import pytest

from test_acceptance_data import live, configured_node
from test_workflow_external_sql import fixture_config
from test_acceptance_oracle_tls import image_for
from taskconsole.workflows_sql import create_connection, connect
from taskconsole.workflows_n8n import execute_graph
from taskconsole.workflows import public_run


pytestmark = pytest.mark.skipif(
    os.environ.get('SLEEP_IN_EXTERNAL_SQL_TESTS') != '1'
    or not os.environ.get('SLEEP_IN_N8N_COMMAND')
    or 'oracle' not in os.environ.get('SLEEP_IN_TEST_SQL_DIALECTS', 'postgresql,mysql,oracle').split(','),
    reason='BLOCKED_ENV: actual disposable Oracle Thin and n8n required')


def until(predicate, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.1)
    raise AssertionError('External cancellation boundary was not observed in time')


def test_can04_cancel_after_real_oracle_commit_preserves_effect_and_never_retries(live, record_property):
    import oracledb
    connection = create_connection(live.store, {'name': 'Disposable committed cancellation',
        'dialect': 'oracle', 'config': fixture_config('oracle'), 'write_enabled': True})
    with live.store.transaction() as tx:
        saved = tx.get('connection', connection['id'])
    db = connect(live.store, saved, True)
    table = 'si_cancel_' + uuid.uuid4().hex[:12]
    created = False; thread = None; run = None
    try:
        assert oracledb.is_thin_mode()
        for key, value in {'driver_version': oracledb.__version__, 'server_version': db.version,
            'driver_mode': 'Thin', 'platform': platform.platform(), 'architecture': platform.machine(),
            'fixture_image': image_for('oracle')}.items():
            record_property(key, value)
        with db.cursor() as cursor:
            cursor.execute(f'CREATE TABLE {table} (effect_id VARCHAR2(30 CHAR) PRIMARY KEY)')
            created = True
        source = (f"BEGIN INSERT INTO {table}(effect_id) VALUES ('effect-001'); "
                  'COMMIT; DBMS_SESSION.SLEEP(10); END;')
        marker = live.store.path / 'forbidden-after-committed-cancel'
        sql = {'id': 'committing', 'kind': 'sql', 'source': source, 'inputs': {},
            'config': {'dialect': 'oracle', 'mode': 'write', 'connection_id': connection['id'], 'timeout': 30,
                'retry': {'max_attempts': 3, 'safe_to_retry': True, 'idempotent': True, 'delay_seconds': 0}}}
        downstream = {'id': 'downstream', 'kind': 'python', 'inputs': {}, 'config': {},
            'source': 'from pathlib import Path\ndef main(inputs):\n'
                      f' Path({str(marker)!r}).write_text("must-not-run")\n return {{}}'}
        wf = live.save({'name': 'External commit cancellation boundary', 'timeout': 90,
            'nodes': [sql, downstream], 'edges': [{'source': 'committing', 'target': 'downstream'}]})
        live.publish(wf['id'])
        admitted = live.acceptance_client.post('/api/workflows/' + wf['id'] + '/run',
            json={'idempotency_key': 'committed-external-effect'})
        assert admitted.status_code == 202
        run = admitted.json()
        thread = threading.Thread(target=execute_graph, args=(live, run['id']), daemon=True)
        thread.start()
        def effects():
            with db.cursor() as cursor:
                cursor.execute(f"SELECT COUNT(*) FROM {table} WHERE effect_id='effect-001'")
                return int(cursor.fetchone()[0])
        # Independent actual database observation is the synchronization barrier:
        # cancellation starts only after COMMIT, while the server call still sleeps.
        until(lambda: effects() == 1)
        before = live.get_run(run['id'])
        assert before['nodes']['committing']['status'] == 'running'
        assert before['nodes']['committing'].get('finished_at') is None
        cancel = live.acceptance_client.post('/api/workflow-runs/' + run['id'] + '/cancel')
        assert cancel.status_code == 200 and cancel.json()['status'] == 'cancelling'
        until(lambda: live.get_run(run['id'])['nodes']['committing']['status'] == 'cancelled', timeout=40)
        thread.join(30)
        assert not thread.is_alive()
        result = public_run(live.get_run(run['id']))
        assert result['status'] == 'cancelled'
        assert result['n8n_execution_id'] and result['graph_execution_id']
        assert len(result['nodes']['committing']['attempts']) == 1
        assert live.acceptance_sql_calls['committing'] == 1
        assert result['side_effects_uncertain'] is True
        assert result['review_reason']['code'] == 'check_external_effects'
        assert 'rollback' not in result['nodes']['committing'].get('error', '').lower()
        assert 'rolled back' not in result['nodes']['committing'].get('error', '').lower()
        assert result['nodes']['downstream']['status'] == 'cancelled'
        assert result['nodes']['downstream']['attempts'] == [] and not marker.exists()
        assert effects() == 1
        replay = live.acceptance_client.post('/api/workflows/' + wf['id'] + '/run',
            json={'idempotency_key': 'committed-external-effect'})
        assert replay.status_code == 202 and replay.json()['id'] == run['id']
        execute_graph(live, run['id'])
        assert effects() == 1 and live.acceptance_sql_calls['committing'] == 1
        assert public_run(live.get_run(run['id']))['nodes'] == result['nodes']
        record_property('cancel_boundary', 'Independent effect-001 count1 observed after Oracle COMMIT while DBMS_SESSION.SLEEP was outstanding; cancel cannot undo committed effect, no SQL retry or downstream execution')
    finally:
        try:
            if thread and thread.is_alive():
                live.cancel(run['id']); thread.join(45)
                assert not thread.is_alive(), 'Owned graph did not stop before fixture cleanup'
            db.rollback()
            if created:
                with db.cursor() as cursor:
                    cursor.execute('DROP TABLE ' + table + ' PURGE')
        finally:
            db.close()
