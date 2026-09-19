"""Software-only native recovery: disposable state, owned processes, no power changes."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from test_workflow_api_contract import client, admin, saved
from test_acceptance_schedule_security import utc, execute, rows
from test_acceptance_local_recovery import supervisor, until, alive, publish, finished
from taskconsole.local import read_json, write_json, status, request_stop


def restarted_tick(store, moment):
    """A fresh interpreter reads the durable cursor; no clock patch or shared service."""
    code = ('import json,sys\nfrom datetime import datetime\n'
            'from taskconsole.store import Store\nfrom taskconsole.workflows import WorkflowService\n'
            'store=Store(sys.argv[1],sys.argv[2])\n'
            'try: print(json.dumps(WorkflowService(store).tick(datetime.fromisoformat(sys.argv[3]))))\n'
            'finally: store.engine.dispose()\n')
    result = subprocess.run([sys.executable, '-c', code, str(store.path), str(store.engine.url),
                             utc(moment).isoformat()], capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


@pytest.mark.parametrize('wall,after,expected,absent', [
    ('02:30', '2026-03-08T07:00Z', ['2026-03-09T07:30Z', '2026-03-10T07:30Z'],
     ['2026-03-08T08:30Z']),
    ('01:30', '2026-11-01T05:00Z', ['2026-11-01T06:30Z', '2026-11-02T07:30Z'],
     ['2026-11-01T07:30Z']),
])
def test_mac_rc08_dst_pause_offline_process_restart_and_product_timezone(client, wall, after, expected, absent):
    admin(client); wf = saved(client); prefix = '/api/workflows/' + wf['id']
    assert client.post(prefix + '/publish').status_code == 200
    wf.update(schedule={'kind': 'daily', 'time': wall}, timezone='America/Chicago', enabled=True)
    assert client.put(prefix, json=wf).status_code == 200
    svc = client.app.state.workflows
    preview = client.post(prefix + '/preview', json={'after': after})
    assert preview.status_code == 200
    assert [utc(x) for x in preview.json()['next_runs'][:2]] == [utc(x) for x in expected]
    assert restarted_tick(svc.store, after) == []
    # Spring has no occurrence in the gap. Fall admits the first fold only.
    if wall == '02:30':
        assert restarted_tick(svc.store, absent[0]) == []
    from datetime import timedelta
    first = utc(expected[0])
    restarted_tick(svc.store, (first - timedelta(seconds=1)).isoformat())
    admitted = restarted_tick(svc.store, first.isoformat())
    assert len(admitted) == 1 and utc(admitted[0]['scheduled_at']) == first
    execute(svc, admitted[0])
    assert restarted_tick(svc.store, first.isoformat()) == []
    if wall == '01:30':
        assert restarted_tick(svc.store, absent[0]) == []
    # Pause through the next real wall time via the authenticated product API.
    wf = client.get(prefix).json(); wf['enabled'] = False
    assert client.put(prefix, json=wf).status_code == 200
    assert restarted_tick(svc.store, expected[1]) == []
    wf['enabled'] = True
    assert client.put(prefix, json=wf).status_code == 200
    assert restarted_tick(svc.store, (utc(expected[1]) + timedelta(minutes=5)).isoformat()) == []
    # A real offline gap crossing the following due time is skipped after restart.
    offline_end = utc(expected[1]) + timedelta(days=1, minutes=10)
    assert restarted_tick(svc.store, offline_end.isoformat()) == []
    gaps = client.get(prefix + '/schedule-history').json()['items']
    assert any(item['kind'] == 'gap' and item['reason'] == 'offline_gap_skipped' for item in gaps)
    # Product timezone change uses a deliberately independent, known UTC table.
    wf.update(timezone='Asia/Shanghai', schedule={'kind': 'daily', 'time': '20:00'})
    assert client.put(prefix, json=wf).status_code == 200
    due = offline_end.replace(hour=12, minute=0, second=0)
    preview = client.post(prefix + '/preview', json={'after': offline_end.isoformat()})
    assert preview.status_code == 200 and utc(preview.json()['next_runs'][0]) == due
    assert restarted_tick(svc.store, (due - timedelta(seconds=1)).isoformat()) == []
    changed = restarted_tick(svc.store, due.isoformat())
    assert len(changed) == 1 and utc(changed[0]['scheduled_at']) == due
    execute(svc, changed[0])
    assert restarted_tick(svc.store, due.isoformat()) == []
    assert sorted(utc(run['scheduled_at']) for run in rows(svc, 'workflow_run')) == [first, due]
    assert len(rows(svc, 'workflow_occurrence')) == 2


requires_supervisor = pytest.mark.skipif(not os.environ.get('SLEEP_IN_TEST_N8N_COMMAND'),
    reason='BLOCKED_ENV: actual pinned Node/n8n supervisor fixture required')


@requires_supervisor
def test_mac_rc10_live_lock_and_reused_pid_record_never_kill_unrelated_process(supervisor):
    s = supervisor
    unrelated = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(180)'],
                                 cwd=s.state.parent, start_new_session=True)
    try:
        same = s.launch()
        assert same['pid'] == s.initial['pid'] and same['generation'] == s.initial['generation']
        assert same['children'] == s.initial['children']
        competing = subprocess.run([sys.executable, '-m', 'taskconsole.local', '--state', str(s.state), 'serve'],
                                   capture_output=True, text=True, timeout=10)
        assert competing.returncode == 1 and 'already running' in competing.stderr
        assert status(s.state)['children'] == s.initial['children']
        request_stop(s.state, 'finish')
        until(lambda: not alive(s.initial['pid']), timeout=30)
        assert all(not alive(pid) for pid in s.initial['children'].values())
        old_seen = time.time() - 3600
        write_json(s.state / 'local-status.json', {'state': 'running', 'assertion': True,
            'pid': unrelated.pid, 'generation': 'former-owner', 'seen_at': old_seen,
            'children': {'app': unrelated.pid, 'worker': unrelated.pid}})
        stale = status(s.state)
        assert stale['state'] == 'stopped' and stale['assertion'] is False and stale['components'] == {}
        assert unrelated.poll() is None
        replacement = s.launch('--explicit')
        assert replacement['pid'] not in {s.initial['pid'], unrelated.pid}
        assert replacement['generation'] != s.initial['generation']
        assert replacement['recovery']['previous_state'] == 'running'
        assert replacement['recovery']['previous_seen'] == old_seen
        assert replacement['recovery']['restored_at'] > old_seen
        assert unrelated.poll() is None
        successful = finished(s, s.svc.admit(s.template['id'], key='recovered-pid-owner')['id'])
        assert successful['status'] == 'succeeded' and successful['n8n_execution_id']
        assert unrelated.poll() is None
    finally:
        if unrelated.poll() is None:
            unrelated.terminate(); unrelated.wait(timeout=5)


@requires_supervisor
def test_mac_rc11_status_persistence_failure_preserves_history_preferences_and_effect_dedup(supervisor):
    s = supervisor; ledger = s.state / 'completed-effects.txt'
    wf = publish(s, 'from pathlib import Path\ndef main(inputs):\n'
        f' with Path({str(ledger)!r}).open("a") as out: out.write("committed-once\\n")\n'
        ' return {"committed":True}')
    admitted = s.svc.admit(wf['id'], key='completed-before-status-failure')
    completed = finished(s, admitted['id'])
    assert completed['status'] == 'succeeded' and completed['n8n_execution_id']
    preferences = read_json(s.state / 'local-preferences.json')
    target = s.state / 'local-status.json'
    saved_status = s.state / 'pre-fault-status.json'
    target.replace(saved_status); target.mkdir()
    try:
        # Only this disposable status target is unavailable. No disk fill, mount,
        # global permission changes, or application/database monkeypatch is used.
        until(lambda: not alive(s.initial['pid']), timeout=35)
        assert all(not alive(pid) for pid in s.initial['children'].values())
        result = subprocess.run([sys.executable, '-m', 'taskconsole.local', '--state', str(s.state), 'status'],
                                capture_output=True, text=True, timeout=10)
        assert result.returncode != 0 and 'local-status.json' in result.stderr
        assert 'directory' in result.stderr.lower()
        assert 'running' not in result.stdout
        assert 'local-status.json' in (s.state / 'local-launch.log').read_text()
        assert read_json(s.state / 'local-preferences.json') == preferences
        assert s.svc.get_run(completed['id']) == completed
        assert ledger.read_text().splitlines() == ['committed-once']
    finally:
        target.rmdir(); saved_status.replace(target)
    replacement = s.launch()
    assert replacement['state'] == 'running' and replacement['generation'] != s.initial['generation']
    assert replacement['running_preference'] is True
    assert s.svc.get_run(completed['id']) == completed
    assert s.svc.admit(wf['id'], key='completed-before-status-failure')['id'] == completed['id']
    assert finished(s, s.svc.admit(s.template['id'], key='after-status-restoration')['id'])['status'] == 'succeeded'
    assert ledger.read_text().splitlines() == ['committed-once']
    with s.store.engine.connect() as connection:
        assert connection.exec_driver_sql('PRAGMA integrity_check').scalar() == 'ok'
