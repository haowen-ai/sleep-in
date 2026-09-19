"""Missed schedule history uses real stores/HTTP; all dates and data are synthetic."""
from datetime import timedelta

import pytest
from test_workflow_api_contract import client, admin, saved
from test_acceptance_schedule_security import utc, rows, scheduled, execute
from taskconsole.workflows import WorkflowService


def configure(client, policy='skip', times=None):
    admin(client)
    wf = saved(client)
    prefix = '/api/workflows/' + wf['id']
    assert client.post(prefix + '/publish').status_code == 200
    wf['triggers'] = [{'id': 'clock', 'kind': 'scheduled', 'enabled': True,
        'schedule': {'kind': 'daily', 'times': times or ['07:00', '08:00', '09:00']},
        'timezone': 'UTC', 'missed_policy': policy, 'grace_seconds': 7200,
        'params': {'private': 'history-must-not-expose-parameters'}}]
    assert client.put(prefix, json=wf).status_code == 200
    return client.app.state.workflows, wf, prefix


def history(client, prefix):
    response = client.get(prefix + '/schedule-history')
    assert response.status_code == 200, response.text
    assert 'history-must-not-expose-parameters' not in response.text
    return response.json()['items']


def test_default_skip_history_covers_all_missed_times_and_future_admission(client):
    svc, wf, prefix = configure(client)
    svc.tick(utc('2026-09-21T06:59:59Z'))
    # A new service object reads the durable cursor, just as a process restart does.
    svc = WorkflowService(svc.store)
    assert svc.tick(utc('2026-09-21T09:10Z')) == []
    items = history(client, prefix)
    assert len(items) == 1
    gap = items[0]
    assert (gap['kind'], gap['status'], gap['reason']) == ('gap', 'skipped', 'offline_gap_skipped')
    assert gap['timezone'] == 'UTC' and gap['schedule']['times'] == ['07:00', '08:00', '09:00']
    assert gap['until_inclusive'] is True
    for expected in ['07:00', '08:00', '09:00']:
        assert utc(gap['from']) < utc('2026-09-21T' + expected + 'Z') <= utc(gap['until'])
    assert rows(svc, 'workflow_run') == []
    assert WorkflowService(svc.store).tick(utc('2026-09-21T09:10Z')) == []
    assert history(client, prefix) == items
    svc.tick(utc('2026-09-22T06:59:59Z'))
    run = svc.tick(utc('2026-09-22T07:00Z'))[0]
    assert run['scheduled_at'] == utc('2026-09-22T07:00Z').isoformat()
    assert client.get(prefix).json()['triggers'][0]['enabled'] is True
    item = next(x for x in history(client, prefix) if x['kind'] == 'occurrence')
    assert item['status'] == 'admitted' and item['run_id'] == run['id']
    assert item['occurrence'] == run['scheduled_at']


@pytest.mark.parametrize('recovered,expected', [
    ('2026-09-21T09:10Z', '2026-09-21T09:00Z'),
    ('2026-09-21T11:00Z', '2026-09-21T09:00Z'),
    ('2026-09-21T11:00:01Z', None),
])
def test_latest_only_recovery_history_and_restart_deduplication(client, recovered, expected):
    svc, wf, prefix = configure(client, 'latest_once')
    svc.tick(utc('2026-09-21T06:59:59Z'))
    admitted = WorkflowService(svc.store).tick(utc(recovered))
    assert len(admitted) == (1 if expected else 0)
    items = history(client, prefix)
    gaps = [x for x in items if x['kind'] == 'gap']
    assert len(gaps) == 1 and gaps[0]['status'] == 'skipped'
    gap = gaps[0]
    assert utc(gap['from']) == utc('2026-09-21T06:59:59Z')
    if expected:
        assert gap['until_inclusive'] is False and utc(gap['until']) == utc(expected)
        assert admitted[0]['scheduled_at'] == utc(expected).isoformat()
        assert [x['run_id'] for x in items if x['kind'] == 'occurrence'] == [admitted[0]['id']]
        execute(svc, admitted[0])
    else:
        assert gap['until_inclusive'] is True
        assert utc(gap['until']) >= utc('2026-09-21T09:00Z')
    assert WorkflowService(svc.store).tick(utc(recovered)) == []
    assert WorkflowService(svc.store).tick(utc(recovered) + timedelta(seconds=1)) == []
    assert history(client, prefix) == items
    assert len(rows(svc, 'workflow_run')) == (1 if expected else 0)


def test_history_auth_acl_and_safe_field_projection(client):
    svc, wf, prefix = configure(client)
    wf['allowed_user_ids'] = ['other-user']
    assert client.put(prefix, json=wf).status_code == 200
    with svc.store.transaction() as tx:
        tx.put('workflow_event', {'id': 'private-event', 'workflow_id': wf['id'],
            'trigger_id': 'clock', 'reason': 'offline_gap_skipped', 'from': '2026-09-21T07:00Z',
            'until': '2026-09-21T08:00Z', 'created_at': '2026-09-21T09:00Z',
            'params': {'password': 'private-parameter'}, 'error': 'private-connection-string',
            'schedule': {'kind': 'daily', 'time': '07:00', 'private': 'private-schedule-extension'},
            'callback_token': 'private-callback', 'claim': 'private-claim'})
    response = client.get(prefix + '/schedule-history')
    assert response.status_code == 200
    assert not any(value in response.text for value in ['private-parameter', 'private-connection-string', 'private-callback', 'private-claim', 'private-schedule-extension'])
    before = rows(svc, 'workflow_event')
    assert client.post('/api/admin/users', json={'username': 'reader', 'password': 'synthetic-reader-password', 'role': 'operator'}).status_code == 200
    client.post('/api/logout')
    assert client.get(prefix + '/schedule-history').status_code == 401
    login = client.post('/api/login', json={'username': 'reader', 'password': 'synthetic-reader-password'})
    client.headers['X-CSRF-Token'] = login.json()['csrf']
    assert client.get(prefix + '/schedule-history').status_code == 403
    assert rows(svc, 'workflow_event') == before
    assert rows(svc, 'workflow_run') == []


def test_history_keeps_trigger_timezone_and_schedule_at_time_of_gap(client):
    svc, wf, prefix = configure(client)
    wf['triggers'][0]['timezone'] = 'America/Chicago'
    assert client.put(prefix, json=wf).status_code == 200
    svc.tick(utc('2026-09-21T11:59:59Z'))
    assert svc.tick(utc('2026-09-21T14:10Z')) == []
    original = history(client, prefix)
    assert len(original) == 1 and original[0]['timezone'] == 'America/Chicago'
    wf['triggers'][0]['timezone'] = 'Asia/Shanghai'
    wf['triggers'][0]['schedule'] = {'kind': 'daily', 'time': '23:00'}
    assert client.put(prefix, json=wf).status_code == 200
    assert history(client, prefix) == original
    with svc.store.transaction() as tx:
        user = next(row for row in tx.all('user') if row['username'] == 'owner')
        user['role'] = 'operator'
        tx.put('user', user)
    assert history(client, prefix) == original
