"""Cases SCH-002..020 and invalid/boundary variants with independent UTC oracles."""
from datetime import datetime
import pytest
from taskconsole.schedule import workflow_next_runs


def utc(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


@pytest.mark.parametrize('spec,zone,after,expected', [
    ({'kind':'interval','every':15,'anchor':'2026-09-21T08:00Z'},'UTC','2026-09-21T08:07Z',['2026-09-21T08:15Z','2026-09-21T08:30Z']),
    ({'kind':'interval','every':15,'anchor':'2026-09-21T08:00Z'},'UTC','2026-09-21T08:15Z',['2026-09-21T08:30Z','2026-09-21T08:45Z']),
    ({'kind':'interval','every':2,'unit':'hours','anchor':'2026-09-21T08:00Z'},'UTC','2026-09-21T08:01Z',['2026-09-21T10:00Z','2026-09-21T12:00Z']),
    ({'kind':'daily','time':'07:00'},'UTC','2026-09-20T22:00Z',['2026-09-21T07:00Z','2026-09-22T07:00Z']),
    ({'kind':'daily','times':['18:30','07:00','07:00']},'UTC','2026-09-20T22:00Z',['2026-09-21T07:00Z','2026-09-21T18:30Z']),
    ({'kind':'weekdays','time':'07:00'},'UTC','2026-09-25T08:00Z',['2026-09-28T07:00Z','2026-09-29T07:00Z']),
    ({'kind':'weekly','weekdays':[0,2],'time':'07:00'},'UTC','2026-09-20T00:00Z',['2026-09-21T07:00Z','2026-09-23T07:00Z']),
    ({'kind':'monthly','day':31,'time':'07:00'},'UTC','2026-04-01T00:00Z',['2026-05-31T07:00Z','2026-07-31T07:00Z']),
    ({'kind':'monthly','day':'last','time':'07:00'},'UTC','2026-02-01T00:00Z',['2026-02-28T07:00Z','2026-03-31T07:00Z']),
    ({'kind':'monthly','day':'last','time':'07:00'},'UTC','2028-02-01T00:00Z',['2028-02-29T07:00Z','2028-03-31T07:00Z']),
    ({'kind':'daily','time':'07:00'},'America/Chicago','2026-09-20T00:00Z',['2026-09-20T12:00Z','2026-09-21T12:00Z']),
    ({'kind':'daily','time':'07:00'},'Asia/Shanghai','2026-09-20T00:00Z',['2026-09-20T23:00Z','2026-09-21T23:00Z']),
    ({'kind':'daily','time':'02:30'},'America/Chicago','2026-03-08T00:00Z',['2026-03-09T07:30Z','2026-03-10T07:30Z']),
    ({'kind':'daily','time':'01:30'},'America/Chicago','2026-10-31T12:00Z',['2026-11-01T06:30Z','2026-11-02T07:30Z']),
])
def test_fixed_schedule_oracles(spec,zone,after,expected):
    assert workflow_next_runs(spec,zone,utc(after),count=2)==[utc(x) for x in expected]


def test_once_completes_and_inclusive_start_end():
    spec={'kind':'once','date':'2026-09-21','time':'07:00'}
    assert workflow_next_runs(spec,'UTC',utc('2026-09-20T00:00Z'))==[utc('2026-09-21T07:00Z')]
    assert workflow_next_runs(spec,'UTC',utc('2026-09-21T07:00Z'))==[]
    bound={'kind':'daily','time':'07:00','start':'2026-09-21T07:00Z','end':'2026-09-22T07:00Z'}
    assert workflow_next_runs(bound,'UTC',utc('2026-09-20T00:00Z'))==[utc('2026-09-21T07:00Z'),utc('2026-09-22T07:00Z')]
    assert workflow_next_runs(bound,'UTC',utc('2026-09-23T07:00Z'))==[]


def test_past_end_without_start_is_expired_not_invalid():
    spec={'kind':'daily','time':'07:00','end':'2026-09-21T07:00Z'}
    assert workflow_next_runs(spec,'UTC',utc('2026-09-22T07:00Z'))==[]


@pytest.mark.parametrize('spec', [
    {'kind':'interval','every':0}, {'kind':'interval','every':True}, {'kind':'interval','every':1.5},
    {'kind':'daily','time':'25:00'}, {'kind':'daily','time':'7:00'}, {'kind':'daily'},
    {'kind':'daily','times':[]}, {'kind':'daily','times':'07:00'},
    {'kind':'weekly','weekdays':[],'time':'07:00'}, {'kind':'weekly','weekdays':[True],'time':'07:00'},
    {'kind':'monthly','day':32,'time':'07:00'}, {'kind':'cron','cron':'* * * * *'},
    {'kind':'once','date':'2026-02-30','time':'07:00'},
    {'kind':'daily','time':'07:00','start':'2026-09-22T00:00Z','end':'2026-09-21T00:00Z'},
])
def test_bad_forms_rejected(spec):
    with pytest.raises(ValueError):
        workflow_next_runs(spec,'UTC',utc('2026-09-20T00:00Z'))


def test_invalid_timezone_is_validation_error():
    with pytest.raises(ValueError):
        workflow_next_runs({'kind':'daily','time':'07:00'},'Moon/Sea',utc('2026-09-20T00:00Z'))
