"""One timezone policy for both schedule preview and dispatch."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from croniter import croniter

UTC = timezone.utc
KINDS = {'manual', 'interval', 'daily', 'weekdays', 'weekly', 'monthly', 'cron'}


def validate_schedule(spec, tz):
    try:
        ZoneInfo(tz)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        raise ValueError('Invalid IANA timezone')
    kind = spec.get('kind')
    if kind not in KINDS:
        raise ValueError('Invalid schedule kind')
    if kind == 'interval':
        if type(spec.get('every')) is not int or not 1 <= spec['every'] <= 525600:
            raise ValueError('Interval must be 1–525600 minutes')
    if kind in {'daily', 'weekdays', 'weekly', 'monthly'}:
        try:
            value = spec['time']
            if len(value) != 5 or value[2] != ':':
                raise ValueError()
            datetime.strptime(value, '%H:%M')
        except (KeyError, ValueError, TypeError):
            raise ValueError('Time must use HH:MM')
    if kind == 'weekly':
        days = spec.get('weekdays')
        if not isinstance(days, list) or not days or any(type(d) is not int or not 0 <= d <= 6 for d in days):
            raise ValueError('Choose at least one weekday')
    if kind == 'monthly' and (type(spec.get('day')) is not int or not 1 <= spec['day'] <= 31):
        raise ValueError('Day must be 1–31')
    if kind == 'cron':
        expression = spec.get('cron', '')
        if not isinstance(expression, str):
            raise ValueError('Cron expression must be text')
        parts = expression.split()
        if len(parts) != 5 or not croniter.is_valid(expression) or any(x in expression.upper() for x in ['#', 'L', '?']):
            raise ValueError('Use a standard five-field cron expression')
        if parts[2] != '*' and parts[4] != '*':
            raise ValueError('Restrict either day of month or weekday, not both')


def next_runs(spec, timezone_name, after, anchor=None, count=5):
    validate_schedule(spec, timezone_name)
    if after.tzinfo is None:
        raise ValueError('after must be timezone aware')
    kind = spec['kind']
    after = after.astimezone(UTC)
    if kind == 'manual':
        return []
    if kind == 'interval':
        step = timedelta(minutes=spec['every'])
        base = (anchor or after).astimezone(UTC)
        first = base + step * max(1, (after - base) // step + 1)
        return [first + step * n for n in range(count)]
    if kind == 'cron':
        expression = spec['cron']
    else:
        hour, minute = map(int, spec['time'].split(':'))
        day = str(spec['day']) if kind == 'monthly' else '*'
        weekday = '1-5' if kind == 'weekdays' else ','.join(str((x + 1) % 7) for x in sorted(set(spec.get('weekdays', [])))) if kind == 'weekly' else '*'
        expression = f'{minute} {hour} {day} * {weekday}'
    zone = ZoneInfo(timezone_name)
    # Enumerate naive wall times, then reject nonexistent local times by roundtrip.
    # fold=0 deliberately chooses the first occurrence of an ambiguous wall time.
    iterator = croniter(expression, after.astimezone(zone).replace(tzinfo=None), max_years_between_matches=8)
    result = []
    for _ in range(20000):
        wall = iterator.get_next(datetime)
        local = wall.replace(tzinfo=zone, fold=0)
        utc = local.astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) != wall or utc <= after:
            continue
        result.append(utc)
        if len(result) == count:
            return result
    raise ValueError('No matching schedule found within supported range')
