from datetime import datetime, timezone
import pytest
from taskconsole.schedule import next_runs, validate_schedule

UTC = timezone.utc

def dt(s):
    return datetime.fromisoformat(s).astimezone(UTC)

def test_monthly_skips_short_months():
    assert next_runs({'kind':'monthly','day':31,'time':'09:00'}, 'UTC', dt('2026-04-01T00:00:00+00:00'),count=2) == [dt('2026-05-31T09:00:00+00:00'),dt('2026-07-31T09:00:00+00:00')]

def test_dst_nonexistent_time_is_skipped():
    result=next_runs({'kind':'daily','time':'02:30'},'America/Chicago',dt('2026-03-07T12:00:00+00:00'),count=1)
    assert result == [dt('2026-03-09T07:30:00+00:00')]

def test_dst_fold_runs_only_first_occurrence():
    spec={'kind':'daily','time':'01:30'}
    assert next_runs(spec,'America/Chicago',dt('2026-11-01T05:00:00+00:00'),count=2)==[dt('2026-11-01T06:30:00+00:00'),dt('2026-11-02T07:30:00+00:00')]
    assert next_runs(spec,'America/Chicago',dt('2026-11-01T07:00:00+00:00'),count=1)==[dt('2026-11-02T07:30:00+00:00')]

def test_interval_uses_elapsed_time_anchor():
    assert next_runs({'kind':'interval','every':90},'America/Chicago',dt('2026-11-01T06:35:00+00:00'),dt('2026-11-01T05:00:00+00:00'),1)==[dt('2026-11-01T08:00:00+00:00')]

def test_weekdays_and_weekly():
    assert next_runs({'kind':'weekdays','time':'09:15'},'Asia/Shanghai',dt('2026-09-18T02:00:00+00:00'),count=1)==[dt('2026-09-21T01:15:00+00:00')]
    assert next_runs({'kind':'weekly','time':'09:15','weekdays':[0,4]},'UTC',dt('2026-09-18T10:00:00+00:00'),count=1)==[dt('2026-09-21T09:15:00+00:00')]

@pytest.mark.parametrize('spec,tz', [({'kind':'cron','cron':'0 0 1 * 1'},'UTC'),({'kind':'cron','cron':'* * * * * *'},'UTC'),({'kind':'interval','every':0},'UTC'),({'kind':'weekly','weekdays':[],'time':'12:00'},'UTC'),({'kind':'daily','time':'24:00'},'UTC'),({'kind':'manual'},'Not/AZone')])
def test_invalid_schedules_rejected(spec,tz):
    with pytest.raises(ValueError): validate_schedule(spec,tz)

def test_manual_has_no_next_run():
    assert next_runs({'kind':'manual'},'UTC',datetime.now(UTC))==[]
