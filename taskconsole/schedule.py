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


def normalize_workflow_schedule(spec, timezone_name):
    """Persist bounds as instants; never infer a local time from the host zone."""
    import copy
    if not isinstance(spec,dict):raise ValueError('Schedule must be an object')
    try:zone=ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError,TypeError,ValueError):raise ValueError('Invalid IANA timezone')
    result=copy.deepcopy(spec)
    if isinstance(result.get('times'),list) and all(isinstance(value,str) for value in result['times']):
        result['times']=sorted(set(result['times']))
    for key in ('start','end','anchor'):
        value=result.get(key)
        if not value:continue
        try:parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
        except (AttributeError,TypeError,ValueError):raise ValueError(key.title()+' must be an ISO date and time')
        if parsed.tzinfo is None:
            local=parsed.replace(tzinfo=zone,fold=0)
            instant=local.astimezone(UTC)
            if instant.astimezone(zone).replace(tzinfo=None)!=parsed:
                raise ValueError(key.title()+' is a nonexistent local time in '+timezone_name+'; choose an existing time')
            parsed=instant
        result[key]=parsed.astimezone(UTC).isoformat()
    return result


def workflow_next_runs(spec, timezone_name, after, count=5):
    """Form-only v2 calculator; v1 Cron remains isolated above."""
    import calendar
    try: zone = ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError,TypeError,ValueError): raise ValueError('Invalid IANA timezone')
    spec=normalize_workflow_schedule(spec,timezone_name)
    if after.tzinfo is None:
        raise ValueError('after must be timezone aware')
    kind = spec.get('kind', 'manual')
    if kind not in {'manual','interval','daily','weekdays','weekly','monthly','once'}:
        raise ValueError('Choose a form schedule; Cron is unsupported')
    if kind == 'manual': return []
    def parse(value):
        result = datetime.fromisoformat(value.replace('Z','+00:00'))
        if result.tzinfo is None: result = result.replace(tzinfo=zone)
        return result.astimezone(UTC)
    start = parse(spec['start']) if spec.get('start') else after
    end = parse(spec['end']) if spec.get('end') else None
    if end and spec.get('start') and end < start: raise ValueError('End must follow start')
    if end and end <= after: return []
    if kind == 'interval':
        every = spec.get('every')
        if type(every) is not int or not 1 <= every <= 525600: raise ValueError('Interval must be positive')
        if spec.get('unit','minutes') not in {'minutes','hours'}: raise ValueError('Invalid interval unit')
        step = timedelta(minutes=every * (60 if spec.get('unit') == 'hours' else 1))
        base = parse(spec['anchor']) if spec.get('anchor') else start
        lower = max(after,start-timedelta(microseconds=1))
        first = base + step * max(0, (lower-base)//step+1)
        return [x for n in range(count) if (x := first+step*n) and (not end or x<=end)]
    times = spec.get('times') if 'times' in spec else [spec.get('time')]
    if not isinstance(times,list) or not times: raise ValueError('Choose at least one HH:MM time')
    parsed = []
    for value in times:
        try:
            if not isinstance(value,str) or len(value)!=5 or value[2]!=':':raise ValueError()
            parsed.append(datetime.strptime(value,'%H:%M').time())
        except (ValueError,TypeError): raise ValueError('Time must use HH:MM')
    if kind == 'weekly' and (not spec.get('weekdays') or any(type(x) is not int or x not in range(7) for x in spec['weekdays'])):
        raise ValueError('Choose weekdays 0 through 6')
    if kind == 'monthly' and spec.get('day') != 'last' and (type(spec.get('day')) is not int or not 1 <= spec['day'] <= 31):
        raise ValueError('Choose day 1–31 or last')
    once = datetime.strptime(spec['date'],'%Y-%m-%d').date() if kind == 'once' else None
    day = max(after,start).astimezone(zone).date()
    result = []
    for offset in range(366*9):
        date = day+timedelta(days=offset)
        if once and date != once: continue
        if kind == 'weekdays' and date.weekday()>4: continue
        if kind == 'weekly' and date.weekday() not in spec['weekdays']: continue
        if kind == 'monthly' and date.day != (calendar.monthrange(date.year,date.month)[1] if spec['day']=='last' else spec['day']): continue
        for clock in sorted(set(parsed)):
            wall = datetime.combine(date,clock)
            utc = wall.replace(tzinfo=zone,fold=0).astimezone(UTC)
            if utc.astimezone(zone).replace(tzinfo=None)!=wall or utc<=after or utc<start: continue
            if end and utc>end: return result
            result.append(utc)
            if len(result)==count: return result
        if once and date>=once: return result
    return result
