"""Late cancellation settlement after an owned committed SQL call outlives n8n.

SQLite supplies real durable effects; a return barrier models the outstanding
transport/server call. This does not claim Oracle driver evidence.
"""
import os
import sqlite3
import threading
import time

import pytest

from test_acceptance_data import live, configured_node
from taskconsole.workflows import public_run
from taskconsole.workflows_n8n import execute_graph
from taskconsole.workflows_sql import create_connection


def until(predicate, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(.05)
    raise AssertionError('Cancellation boundary did not settle before the deadline')


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'), reason='Actual n8n required')
def test_committed_sql_return_after_adapter_exit_settles_cancel_without_replay(live, monkeypatch):
    import taskconsole.workflows as workflows
    connection = create_connection(live.store, {'name': 'Late owned SQL', 'dialect': 'sqlite',
        'config': {'synthetic': True}, 'write_enabled': True})
    database = live.store.path / 'workflow-connections' / (connection['id'] + '.sqlite')
    with sqlite3.connect(database) as db:
        db.execute('CREATE TABLE effects(id TEXT PRIMARY KEY)')
    def effects():
        with sqlite3.connect(database) as db:
            return db.execute('SELECT id FROM effects').fetchall()
    committed = threading.Event(); release = threading.Event()
    original = workflows.execute_sql
    def delayed_return(*args, **kwargs):
        result = original(*args, **kwargs)
        assert effects() == [('effect-001',)]
        committed.set()
        assert release.wait(30), 'Owned SQL return barrier was never released'
        return result
    monkeypatch.setattr(workflows, 'execute_sql', delayed_return)
    graph = {'name': 'Delayed committed cancellation', 'timeout': 90, 'nodes': [
        {'id': 'write', 'kind': 'sql', 'source': "INSERT INTO effects VALUES ('effect-001')", 'inputs': {},
         'config': {'dialect': 'sqlite', 'connection_id': connection['id'], 'mode': 'write',
                    'retry': {'max_attempts': 3, 'safe_to_retry': True, 'idempotent': True, 'delay_seconds': 0}}},
        {'id': 'never', 'kind': 'python', 'source': 'def main(inputs): raise AssertionError("Must not execute")',
         'inputs': {}, 'config': {}}], 'edges': [{'source': 'write', 'target': 'never'}]}
    wf = live.save(graph); live.publish(wf['id'])
    response = live.acceptance_client.post('/api/workflows/' + wf['id'] + '/run',
                                           json={'idempotency_key': 'once'})
    assert response.status_code == 202
    rid = response.json()['id']
    thread = threading.Thread(target=execute_graph, args=(live, rid), daemon=True)
    thread.start()
    try:
        assert committed.wait(25)
        assert effects() == [('effect-001',)]
        cancelled = live.acceptance_client.post('/api/workflow-runs/' + rid + '/cancel')
        assert cancelled.status_code == 200 and cancelled.json()['status'] == 'cancelling'
        # The real adapter's bounded wait expires while the SQL call is still
        # outstanding. No coordinator or manual finish supplies a later callback.
        thread.join(15)
        assert not thread.is_alive()
        pending = live.get_run(rid)
        assert pending['status'] == 'cancelling' and pending['adapter_finished_at']
        assert pending['nodes']['write']['status'] == 'running'
        release.set()
        until(lambda: live.get_run(rid)['nodes']['write']['status'] == 'cancelled')
        result = public_run(live.get_run(rid))
        assert result['status'] == 'cancelled'
        assert result['n8n_execution_id'] and result['graph_execution_id']
        assert result['nodes']['never']['status'] == 'cancelled'
        assert result['nodes']['never']['attempts'] == []
        assert len(result['nodes']['write']['attempts']) == 1
        assert result['nodes']['write']['attempts'][0]['status'] == 'cancelled'
        assert result['side_effects_uncertain'] is True
        assert result['review_reason']['code'] == 'check_external_effects'
        assert live.acceptance_sql_calls['write'] == 1 and effects() == [('effect-001',)]
        replay = live.acceptance_client.post('/api/workflows/' + wf['id'] + '/run',
                                            json={'idempotency_key': 'once'})
        assert replay.status_code == 202 and replay.json()['id'] == rid
        execute_graph(live, rid)
        assert live.acceptance_sql_calls['write'] == 1 and effects() == [('effect-001',)]
        assert public_run(live.get_run(rid))['nodes'] == result['nodes']
    finally:
        release.set()
        live.cancel(rid)
        thread.join(30)
        until(lambda: live.get_run(rid)['nodes']['write']['status'] != 'running')


def test_last_dispatched_node_cancelled_before_start_settles_entire_run(tmp_path):
    from test_workflows import service, simple_graph
    svc = service(tmp_path)
    try:
        from taskconsole.workflows_operations import WorkflowOperations
        channel = WorkflowOperations(svc.store).save_channel({'name': 'Queued only', 'kind': 'webhook',
            'config': {'url': 'http://127.0.0.1:1'}}, 'cancel-channel')
        graph = simple_graph()
        graph['notifications'] = {'channel_ids': [channel['id']], 'events': ['cancelled']}
        wf = svc.save(graph); svc.publish(wf['id'])
        run = svc.admit(wf['id'])
        with svc.store.transaction() as tx:
            record = tx.get('workflow_run', run['id'])
            record['nodes']['a']['status'] = 'dispatching'
            tx.put('workflow_run', record)
        assert svc.cancel(run['id'])['status'] == 'cancelling'
        stopped = svc.execute_node(run['id'], 'a')
        assert stopped['status'] == 'cancelled' and stopped['attempts'] == []
        result = svc.get_run(run['id'])
        assert result['status'] == 'cancelled' and result['finished_at']
        assert all(node['status'] == 'cancelled' and node['attempts'] == []
                   for node in result['nodes'].values())
        before = result.copy()
        svc.execute_node(run['id'], 'a')
        assert svc.get_run(run['id']) == before
        with svc.store.transaction() as tx:
            notices = tx.all('workflow_notification')
        assert len(notices) == 1
        assert notices[0]['run_id'] == run['id'] and notices[0]['event'] == 'cancelled'
        assert notices[0]['status'] == 'pending' and notices[0]['attempts'] == 0
    finally:
        svc.store.engine.dispose()


@pytest.mark.parametrize('remaining_state', ['running', 'dispatching'])
def test_cancelled_node_does_not_finish_run_while_another_node_is_active(tmp_path, remaining_state):
    from test_workflows import service, simple_graph
    svc = service(tmp_path)
    try:
        wf = svc.save(simple_graph()); svc.publish(wf['id'])
        run = svc.admit(wf['id'])
        with svc.store.transaction() as tx:
            record = tx.get('workflow_run', run['id'])
            record['nodes']['a']['status'] = 'dispatching'
            record['nodes']['b']['status'] = remaining_state
            tx.put('workflow_run', record)
        assert svc.cancel(run['id'])['status'] == 'cancelling'
        assert svc.execute_node(run['id'], 'a')['status'] == 'cancelled'
        result = svc.get_run(run['id'])
        assert result['status'] == 'cancelling' and result['finished_at'] is None
        assert result['nodes']['b']['status'] == remaining_state
        with svc.store.transaction() as tx:
            assert tx.get('workflow_notice_seen', run['id']) is None
    finally:
        svc.store.engine.dispose()
