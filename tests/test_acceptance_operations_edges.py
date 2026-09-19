"""Exact migration handoff and retention evidence, entirely disposable state."""
import copy
from datetime import timedelta
import json
from pathlib import Path
import time

import pytest
from test_workflow_migration import fixture as legacy_fixture
from test_workflow_api_contract import client, admin
from test_acceptance_schedule_security import utc
from test_acceptance_artifacts import producer
from taskconsole.store import now
from taskconsole.workflows import WorkflowService
from taskconsole.workflows_migration import WorkflowMigration


def test_ops002_daily_migration_preserves_source_params_zone_history_and_single_dispatch(tmp_path, monkeypatch):
    store = legacy_fixture(tmp_path, {'kind': 'daily', 'time': '07:30'})
    effect = tmp_path / 'migration-effects.jsonl'
    source = 'import json\nfrom pathlib import Path\ndef main(params):\n with Path(' + repr(str(effect)) + ').open("a") as stream: stream.write(json.dumps(params,sort_keys=True)+"\\n")\n'
    original_source = store.path / 'scripts/v1/main.py'
    original_source.write_text(source)
    due = utc('2026-09-21T12:30Z')  # 07:30 Chicago, independently specified.
    historical = {'id': 'legacy-completed', 'task_id': 't1', 'version_id': 'v1', 'status': 'succeeded',
                  'scheduled_at': '2026-09-20T12:30:00+00:00', 'params': {'name': 'prior'}, 'artifacts': []}
    with store.transaction() as tx:
        task = tx.get('task', 't1')
        task.update(timezone='America/Chicago', params={'name': 'Alice', 'nested': [0, False]}, recipients=[],
                    archived=False, next_run=due.isoformat(), script_id='s1', script_name='Legacy script')
        tx.put('task', task)
        tx.put('execution', historical)
    migration = WorkflowMigration(store)
    link = migration.convert('t1')
    svc = WorkflowService(store)
    with store.transaction() as tx:
        wf = tx.get('workflow', link['workflow_id'])
        project = tx.get('source_project', wf['nodes'][0]['config']['project_id'])
        assert tx.get('execution', historical['id']) == historical
        assert tx.get('task', 't1')['enabled'] is True
    assert original_source.read_text() == (Path(project['directory']) / 'main.py').read_text() == source
    assert len(wf['nodes']) == 1 and wf['nodes'][0]['kind'] == 'python'
    assert wf['params'] == {'name': 'Alice', 'nested': [0, False], 'recipients': []}
    assert wf['timezone'] == 'America/Chicago' and wf['schedule'] == {'kind': 'daily', 'time': '07:30'}
    assert wf['enabled'] is False
    assert link['task_id'] == 't1' and link['version_id'] == 'v1'
    svc.publish(wf['id'])
    with pytest.raises(ValueError, match='Migration handoff'):
        svc.admit(wf['id'])
    migration.handoff('t1')
    with store.transaction() as tx:
        wf = tx.get('workflow', wf['id'])
        old = tx.get('task', 't1')
        assert old['enabled'] is False and old['next_run'] is None
        assert wf['enabled'] is False and wf['migration']['handoff_complete'] is True
    wf['enabled'] = True
    svc.save(wf, wf['id'])
    import taskconsole.service as legacy
    monkeypatch.setattr(legacy, 'now', lambda: due)
    svc.tick(due - timedelta(seconds=1))
    assert legacy.tick(store)['dispatched'] == 0
    admitted = svc.tick(due)
    assert len(admitted) == 1 and admitted[0]['scheduled_at'] == due.isoformat()
    assert svc.execute_node(admitted[0]['id'], 'legacy')['status'] == 'succeeded'
    assert svc.finish(admitted[0]['id'])['status'] == 'succeeded'
    assert svc.tick(due) == [] and legacy.tick(store)['dispatched'] == 0
    assert [json.loads(line) for line in effect.read_text().splitlines()] == [wf['params']]
    with store.transaction() as tx:
        assert tx.all('execution') == [historical]
        assert len(tx.all('workflow_run')) == len(tx.all('workflow_occurrence')) == 1
    store.engine.dispose()


def test_ops003_unrepresentable_cron_stays_disabled_with_exact_review_metadata(tmp_path):
    original = {'kind': 'cron', 'cron': '17 4 1 * 1'}
    store = legacy_fixture(tmp_path, original)
    migration = WorkflowMigration(store)
    link = migration.convert('t1')
    with store.transaction() as tx:
        wf = tx.get('workflow', link['workflow_id'])
        task = tx.get('task', 't1')
    assert wf['enabled'] is False and not any(t.get('enabled') for t in wf.get('triggers', []))
    assert wf['schedule'] == {'kind': 'manual'}
    assert link['needs_schedule_review'] is True and link['original_schedule'] == original
    assert wf['migration']['original_schedule'] == original
    assert task['schedule'] == original and task['enabled'] is True
    migration.handoff('t1')
    with store.transaction() as tx:
        wf = tx.get('workflow', wf['id'])
        assert tx.get('task', 't1')['enabled'] is False
    assert wf['enabled'] is False and wf['migration']['needs_schedule_review'] is True
    assert WorkflowService(store).tick(utc('2026-09-21T04:17Z')) == []
    with store.transaction() as tx:
        assert tx.all('workflow_run') == []
    store.engine.dispose()


def test_ops004_cleanup_audit_preserves_actual_retry_artifact_and_both_attempt_logs(client):
    admin(client)
    svc = client.app.state.workflows
    def completed(days):
        node = producer('source', content='expired artifact', extra='print("historical worker log")\n')
        wf = svc.save({'name': 'Retention historical', 'nodes': [node], 'edges': []})
        svc.publish(wf['id'])
        run = svc.admit(wf['id'])
        assert svc.execute_node(run['id'], 'source')['status'] == 'succeeded'
        svc.finish(run['id'])
        with svc.store.transaction() as tx:
            stored = tx.get('workflow_run', run['id'])
            stored['created_at'] = stored['finished_at'] = (now() - timedelta(days=days)).isoformat()
            tx.put('workflow_run', stored)
        return run['id']
    old = completed(40)
    obsolete = completed(100)
    source = producer('source', content='retry needs these bytes', extra='print("producer retained log")\n')
    consumer = {'id': 'consumer', 'kind': 'python', 'inputs': {'file': {'source': 'artifact', 'node_id': 'source', 'name': 'same.txt'}},
        'config': {'retry': {'max_attempts': 2, 'safe_to_retry': True, 'delay_seconds': 0}},
        'source': 'import os,sys\nfrom pathlib import Path\ndef main(inputs):\n attempt=Path(os.environ["SLEEP_IN_INPUT_FILE"]).parent.name\n print("consumer-"+attempt)\n print("stderr-"+attempt,file=sys.stderr)\n if attempt=="attempt-1": raise RuntimeError("safe synthetic retry")\n return {"text":Path(inputs["file"]["path"]).read_text()}'}
    wf = svc.save({'name': 'Active retry retention', 'nodes': [source, consumer], 'edges': [{'source': 'source', 'target': 'consumer'}]})
    svc.publish(wf['id'])
    active = svc.admit(wf['id'])
    assert svc.execute_node(active['id'], 'source')['status'] == 'succeeded'
    failed = svc.execute_node(active['id'], 'consumer')
    assert failed['status'] == 'failed' and svc.node_status(active['id'], 'consumer')['retry_ready'] is True
    before = svc.get_run(active['id'])
    artifact = Path(before['artifacts'][0]['path'])
    before_bytes = artifact.read_bytes()
    response = client.post('/api/workflow-maintenance/cleanup')
    assert response.status_code == 200, response.text
    result = response.json()
    assert result == {'data_run_ids': sorted([old, obsolete]), 'metadata_run_ids': [obsolete], 'protected_run_ids': [active['id']]}
    with svc.store.transaction() as tx:
        audit = tx.get('meta', 'workflow_cleanup')
        assert audit['at'] and audit['result'] == result
        expired = tx.get('workflow_run', old)
        assert expired['data_expired'] is True and expired['artifacts'] == []
        assert 'stdout' not in expired['nodes']['source'] and 'stdout' not in expired['nodes']['source']['attempts'][0]
        assert tx.get('workflow_run', obsolete) is None
    assert not (svc.store.path / 'workflow-runs' / old).exists()
    assert not (svc.store.path / 'workflow-runs' / obsolete).exists()
    assert artifact.read_bytes() == before_bytes
    assert svc.get_run(active['id']) == before
    svc.submit_node(active['id'], 'consumer', retry=True)
    deadline = time.monotonic() + 10
    while svc.get_run(active['id'])['nodes']['consumer']['status'] in {'dispatching', 'running'} and time.monotonic() < deadline:
        time.sleep(.02)
    final = svc.finish(active['id'])
    assert final['status'] == 'succeeded'
    assert final['nodes']['consumer']['output']['data'] == {'text': 'retry needs these bytes'}
    attempts = final['nodes']['consumer']['attempts']
    assert [attempt['status'] for attempt in attempts] == ['failed', 'succeeded']
    for index, attempt in enumerate(attempts, 1):
        assert 'consumer-attempt-' + str(index) in attempt['stdout']
        assert 'stderr-attempt-' + str(index) in attempt['stderr']
        directory = svc.store.path / 'workflow-runs' / active['id'] / 'consumer' / ('attempt-' + str(index))
        assert 'consumer-attempt-' + str(index) in (directory / 'stdout.txt').read_text()
    assert 'producer retained log' in final['nodes']['source']['stdout'] and artifact.read_bytes() == before_bytes
