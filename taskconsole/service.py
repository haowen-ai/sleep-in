"""Task validation, immutable execution snapshots, and n8n heartbeat dispatch."""
import hashlib
import json
import re
from datetime import datetime
from .schedule import next_runs, validate_schedule
from .store import now, stamp, uid

ACTIVE = {'queued','running','cancelling'}
TERMINAL = {'succeeded','failed','timed_out','cancelled','interrupted','skipped'}


def audit(tx, user, action, target):
    tx.put('audit', {'id':uid(),'actor':user.get('username','system'),'action':action,'target':target,'created_at':stamp()})


def validate_params(params, manifest, recipients):
    if not isinstance(params,dict) or len(params)>100:
        raise ValueError('Use at most 100 unique parameter keys')
    for key,value in params.items():
        if not isinstance(key,str) or not key.strip() or len(key)>200 or key in {'recipients','TASK_RUN_ID','TASK_OUTPUT_DIR','TASK_PARAMS_FILE'}:
            raise ValueError('Parameter key is empty or reserved')
        if not isinstance(value,str) or len(value.encode())>1048576:
            raise ValueError('Parameter values must be strings, at most 1 MiB each')
    if len(json.dumps(params,ensure_ascii=False).encode())>5*1048576:
        raise ValueError('Parameters exceed 5 MiB')
    definitions=manifest.get('parameters',[]) if isinstance(manifest,dict) else []
    for definition in definitions:
        key=definition['key']
        if definition.get('required') and not params.get(key):
            raise ValueError(f'Required parameter missing: {key}')
    if not isinstance(recipients,list) or len(recipients)>100 or any(not isinstance(x,str) or len(x)>254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+',x) for x in recipients):
        raise ValueError('Recipients must be a list of valid email addresses')


def validate_manifest(manifest):
    if not isinstance(manifest,dict):
        raise ValueError('Manifest must be an object')
    rows=manifest.get('parameters',[])
    if not isinstance(rows,list) or len(rows)>100:
        raise ValueError('Invalid parameter definitions')
    keys=[]
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('key'),str) or not row['key'].strip() or row['key'] in keys or row['key']=='recipients':
            raise ValueError('Parameter definitions require unique keys')
        keys.append(row['key'])
        for field in ('help','default'):
            if field in row and not isinstance(row[field],str):
                raise ValueError('Parameter help and defaults must be text')
    required=manifest.get('required_variables',[])
    if not isinstance(required,list) or any(not isinstance(v,str) or not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*',v) for v in required):
        raise ValueError('Invalid required variable names')
    return manifest


def version_for(tx, version_id):
    version=tx.get('version',version_id)
    if not version or version['status']!='published':
        raise ValueError('Choose an available published script version')
    script=tx.get('script',version['script_id'])
    if not script or script['archived']:
        raise ValueError('Script is archived')
    return version,script


def task_data(tx, data, old=None):
    name=data.get('name','')
    if not isinstance(name,str):
        raise ValueError('Task name must be text')
    name=name.strip()
    if not name or len(name)>100:
        raise ValueError('Task name must be 1–100 characters')
    version,script=version_for(tx,data.get('version_id',''))
    tz=data.get('timezone') or tx.get('meta','settings')['timezone']
    spec=data.get('schedule',{'kind':'manual'})
    validate_schedule(spec,tz)
    params=data.get('params',{})
    recipients=data.get('recipients',[])
    validate_params(params,version.get('manifest',{}),recipients)
    timeout=data.get('timeout',10800)
    if type(timeout) is not int or not 1<=timeout<=86400:
        raise ValueError('Timeout must be 1–86400 seconds')
    enabled=bool(data.get('enabled',False)) and spec['kind']!='manual'
    current=now()
    unchanged=old and old['schedule']==spec and old['timezone']==tz and old['enabled'] and enabled
    anchor=old['anchor'] if unchanged else current.isoformat()
    upcoming=old['next_run'] if unchanged else (next_runs(spec,tz,current,datetime.fromisoformat(anchor),1)[0].isoformat() if enabled else None)
    return {'id':old['id'] if old else uid(),'name':name,'version_id':version['id'],'script_id':script['id'],'script_name':script['name'],'params':params,'recipients':recipients,'schedule':spec,'timezone':tz,'timeout':timeout,'enabled':enabled,'archived':old.get('archived',False) if old else False,'revision':old['revision']+1 if old else 1,'anchor':anchor,'next_run':upcoming,'created_at':old['created_at'] if old else stamp(),'updated_at':stamp(),'last_run':old.get('last_run') if old else None}


def enqueue(tx, task, trigger, actor, dedupe, scheduled_at=None, skipped_reason=None):
    run_id=hashlib.sha256(dedupe.encode()).hexdigest()[:32]
    existing=tx.get('execution',run_id)
    if existing:
        return existing
    overlap=any(r['task_id']==task['id'] and r['status'] in ACTIVE for r in tx.all('execution'))
    reason=skipped_reason or ('overlap' if overlap else None)
    version,_=version_for(tx,task['version_id'])
    result={'id':run_id,'task_id':task['id'],'task_name':task['name'],'script_id':task['script_id'],'script_name':task['script_name'],'version_id':task['version_id'],'revision':task['revision'],'params':{**task['params'],'recipients':task['recipients']},'timezone':task['timezone'],'timeout':task['timeout'],'runtime':version.get('runtime',{}),'status':'skipped' if reason else 'queued','trigger':trigger,'actor':actor,'created_at':stamp(),'scheduled_at':scheduled_at,'started_at':None,'finished_at':stamp() if reason else None,'reason':reason,'exit_code':None,'artifacts':[],'logs_expired':False,'logs_truncated':False,'lease':None}
    tx.put('execution',result)
    task['last_run']={'id':run_id,'status':result['status'],'created_at':result['created_at']}
    tx.put('task',task)
    return result


def tick(store):
    current=now()
    count=0
    with store.transaction() as tx:
        settings=tx.get('meta','settings');settings['last_tick']=current.isoformat();tx.put('meta',settings)
        for task in tx.all('task'):
            if not task['enabled'] or task['archived'] or not task.get('next_run'):
                continue
            due=datetime.fromisoformat(task['next_run'])
            if due>current:
                continue
            late=(current-due).total_seconds()>30
            enqueue(tx,task,'schedule','n8n',f"schedule:{task['id']}:{task['revision']}:{due.isoformat()}",due.isoformat(),'misfire' if late else None)
            task['next_run']=next_runs(task['schedule'],task['timezone'],current,datetime.fromisoformat(task['anchor']),1)[0].isoformat()
            tx.put('task',task);count+=1
    return {'dispatched':count}
