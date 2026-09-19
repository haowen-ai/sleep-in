"""Actual per-run n8n failures degrade engine health without stopping healthy work."""
import json
import os
import signal
import subprocess
import sys

import pytest

from taskconsole.local import status
from test_acceptance_local_recovery import supervisor, until, alive, finished
from test_acceptance_engine_recovery import execution_count

requires_supervisor = pytest.mark.skipif(not os.environ.get('SLEEP_IN_TEST_N8N_COMMAND'),
    reason='BLOCKED_ENV: actual pinned supervisor n8n required')


def publish(s, name, source):
    response = s.client.post('/api/workflows', json={'name': name, 'timeout': 180,
        'nodes': [{'id': 'effect', 'kind': 'python', 'source': source,
                   'inputs': {}, 'config': {}}], 'edges': []})
    assert response.status_code == 200, response.text
    wf = response.json()
    assert s.client.post('/api/workflows/' + wf['id'] + '/publish').status_code == 200
    return wf


def admit(s, wf, key):
    response = s.client.post('/api/workflows/' + wf['id'] + '/run',
                             json={'idempotency_key': key})
    assert response.status_code == 202, response.text
    return response.json()['id']


def wait_health(s, engine_status):
    return until(lambda: (health if (health := s.client.get('/api/workflow-health').json())
                          .get('engine_status') == engine_status else None), timeout=15)


@requires_supervisor
def test_actual_engine_incident_survives_prior_concurrent_success_and_business_failure(supervisor):
    s = supervisor
    failure = publish(s, 'Ordinary business failure', 'def main(inputs): raise ValueError("Business rule declined")')
    business = finished(s, admit(s, failure, 'business-before'))
    assert business['status'] == 'failed'
    healthy = wait_health(s, 'available-cli')
    assert healthy['status'] == 'ready' and healthy['n8n_available'] is True
    assert healthy.get('engine_incident') is None
    assert status(s.state)['state'] == 'running'

    gates = {name: s.state / (name + '-release') for name in ('earlier', 'victim')}
    ledgers = {name: s.state / (name + '-effects') for name in gates}
    workflows = {}
    for name in gates:
        source = ('import os,time\nfrom pathlib import Path\ndef main(inputs):\n'
                  f' with Path({str(ledgers[name])!r}).open("a") as f:\n'
                  '  f.write("effect-001\\n");f.flush();os.fsync(f.fileno())\n'
                  f' while not Path({str(gates[name])!r}).exists(): time.sleep(.02)\n'
                  ' return {"ok":True}\n')
        workflows[name] = publish(s, name, source)
    try:
        earlier = admit(s, workflows['earlier'], 'earlier')
        until(lambda: ledgers['earlier'].exists())
        victim = admit(s, workflows['victim'], 'victim')
        victim_run = until(lambda: (run if (run := s.svc.get_run(victim)).get('adapter_pid')
                                     and run['nodes']['effect'].get('worker_pid')
                                     and ledgers['victim'].exists() else None))
        earlier_pid = s.svc.get_run(earlier)['nodes']['effect']['worker_pid']
        generation = status(s.state)['generation']
        os.kill(victim_run['adapter_pid'], signal.SIGKILL)
        failed = finished(s, victim)
        assert failed['status'] == 'failed'
        incident_health = wait_health(s, 'degraded')
        incident = incident_health['engine_incident']
        assert incident['run_id'] == victim and incident['instance_id'] == generation
        assert incident['code'] == 'adapter_process_exited'
        assert incident_health['status'] == 'ready' and incident_health['n8n_available'] is True
        until(lambda: status(s.state)['state'] == 'degraded', timeout=10)
        assert alive(earlier_pid) and s.svc.get_run(earlier)['status'] == 'running'
        assert ledgers['earlier'].read_text().splitlines() == ['effect-001']
        assert ledgers['victim'].read_text().splitlines() == ['effect-001']
        assert s.client.get('/api/workflow-runs/' + victim).json()['side_effects_uncertain'] is True
        # Opening a running degraded instance must not launch another scheduler
        # or wait for the incident to disappear before returning its honest state.
        opened = subprocess.run([sys.executable, '-m', 'taskconsole.local', '--state', str(s.state), 'start'],
                                capture_output=True, text=True, timeout=15)
        assert opened.returncode == 0, opened.stderr
        reopened = json.loads(opened.stdout)
        assert reopened['state'] == 'degraded' and reopened['pid'] == s.initial['pid']
        assert reopened['generation'] == generation

        gates['earlier'].touch()
        previous = finished(s, earlier)
        assert previous['status'] == 'succeeded' and previous['n8n_execution_id']
        assert previous['started_at'] < incident['observed_at']
        assert wait_health(s, 'degraded')['engine_incident'] == incident
        assert status(s.state)['state'] == 'degraded'
        # A failed business run must neither replace the incident nor count as
        # successful post-incident engine verification.
        later_failure = finished(s, admit(s, failure, 'business-after'))
        assert later_failure['status'] == 'failed'
        assert wait_health(s, 'degraded')['engine_incident'] == incident

        recovery = publish(s, 'Actual successful engine verification', 'def main(inputs): return {"verified":True}')
        recovered = finished(s, admit(s, recovery, 'verify-engine'))
        assert recovered['status'] == 'succeeded' and recovered['n8n_execution_id']
        assert recovered['started_at'] > incident['observed_at']
        verified = wait_health(s, 'verified')
        assert verified.get('engine_incident') is None
        assert verified['engine_verification']['run_id'] == recovered['id']
        until(lambda: status(s.state)['state'] == 'running', timeout=10)
        assert status(s.state)['components']['n8n'] == 'verified'
        assert status(s.state)['pid'] == s.initial['pid']
        # The original incident remains durable and cannot trigger a replay.
        with s.store.transaction() as tx:
            events = [event for event in tx.all('workflow_event')
                      if event.get('reason') == 'engine_incident']
        assert len(events) == 1 and events[0]['run_id'] == victim
        assert admit(s, workflows['victim'], 'victim') == victim
        assert execution_count(s, victim) == execution_count(s, earlier) == 1
        assert ledgers['victim'].read_text().splitlines() == ['effect-001']
        assert len(s.svc.get_run(victim)['nodes']['effect']['attempts']) == 1
    finally:
        for gate in gates.values():
            gate.touch()


@pytest.mark.parametrize('engine_state', ['verified', 'degraded'])
def test_degraded_open_does_not_accept_failed_required_power_component(tmp_path, monkeypatch, engine_state):
    from taskconsole import local
    monkeypatch.setattr(local, 'should_start', lambda *args: True)
    monkeypatch.setattr(local, 'is_running', lambda *args: True)
    monkeypatch.setattr(local, 'app_healthy', lambda *args: True)
    monkeypatch.setattr(local, 'status', lambda *args: {'state': 'degraded',
        'components': {'worker': 'ready', 'app': 'ready', 'n8n': engine_state, 'power': 'failed'}})
    clock = iter([0, 1, 76])
    monkeypatch.setattr(local.time, 'monotonic', lambda: next(clock))
    monkeypatch.setattr(local.time, 'sleep', lambda _: None)
    with pytest.raises(RuntimeError, match='did not become ready'):
        local._start(tmp_path)


def test_old_generation_adapter_cannot_replace_new_owner_engine_evidence(tmp_path):
    from taskconsole.store import Store
    from taskconsole.workflows_n8n import engine_health, record_engine_health
    from taskconsole.local import run_engine_health
    store = Store(tmp_path, 'sqlite:///' + str(tmp_path / 'console.db'))
    try:
        with store.transaction() as tx:
            tx.put('meta', {'id': 'workflow_worker', 'instance_id': 'current'})
            record_engine_health(tx, {'id': 'new-run'}, 'current', incident_code='adapter_process_exited')
            original = tx.get('meta', 'workflow_engine_health')
        for outcome in ('success', 'failure'):
            with store.transaction() as tx:
                record_engine_health(tx, {'id': 'old-run'}, 'old',
                    incident_code='adapter_process_exited' if outcome == 'failure' else None,
                    started_at='2099-01-01T00:00:00+00:00')
                assert tx.get('meta', 'workflow_engine_health') == original
                assert len(tx.all('workflow_event')) == 1
                assert engine_health(tx, 'current')['engine_status'] == 'degraded'
                assert engine_health(tx, 'new-generation')['engine_status'] == 'available-cli'
                assert engine_health(tx, 'new-generation')['engine_incident'] is None
        assert run_engine_health(tmp_path, 'current') == 'degraded'
        assert run_engine_health(tmp_path, 'new-generation') == 'available-cli'
        with store.transaction() as tx:
            assert tx.get('meta', 'workflow_engine_health') == original
    finally:
        store.engine.dispose()
