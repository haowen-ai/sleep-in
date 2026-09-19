"""Workflow publications and authenticated per-node execution, orchestrated by n8n."""
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import secrets
import signal
import threading
import time
from datetime import datetime, timedelta, timezone
from fastapi import Request, HTTPException
from fastapi.responses import JSONResponse, FileResponse
from .store import Store, uid, stamp, now
from .schedule import workflow_next_runs, normalize_workflow_schedule
from .workflows_runtime import LANGUAGES, runtimes, build, run_script, resolve_path, path_tokens, check_schema, validate_schema, portable, WorkerOutputError
from .workflows_sql import create_connection, update_connection, public_connection, execute_sql, connect

ACTIVE={'queued','running','cancelling'}
NODE_TERMINAL={'succeeded','failed','skipped','not_run','cancelled','timed_out'}
IDENTIFIER=re.compile(r'^[A-Za-z0-9_-]{1,100}$')


class WorkflowError(ValueError):
    def __init__(self,message,code='workflow_invalid',node_id=None,field=None,*,pending=None):
        super().__init__(message)
        self.detail={'code':code,'message':message}
        if node_id:self.detail['node_id']=node_id
        if field:self.detail['field']=field
        if pending is not None:self.detail['pending']=pending


def need(tx,kind,key):
    value=tx.get(kind,key)
    if not value:raise WorkflowError('Item not found','not_found')
    return value


def admission_retirement_id(wid,key):
    return hashlib.sha256(json.dumps([wid,'manual',key],ensure_ascii=False).encode()).hexdigest()


def ancestors(nodes,edges):
    parents={n['id']:set() for n in nodes}
    for edge in edges:parents[edge['target']].add(edge['source'])
    result={};visiting=set()
    def visit(key):
        if key in visiting:raise WorkflowError('Graph contains a cycle',node_id=key)
        if key in result:return result[key]
        visiting.add(key);found=set(parents[key])
        for parent in parents[key]:found.update(visit(parent))
        visiting.remove(key);result[key]=found;return found
    for key in parents:visit(key)
    return result


def condition_matches(data,condition):
    value=resolve_path(data,condition.get('path',''))
    op=condition.get('operator');expected=condition.get('value')
    if op=='truthy':
        if type(value) is not bool:raise ValueError('truthy requires a boolean')
        return value
    if type(value)!=type(expected) and not (type(value) in (int,float) and type(expected) in (int,float)):raise ValueError('Condition operand types differ')
    if op in {'gt','gte','lt','lte'} and type(value) not in (int,float):raise ValueError('Ordered condition requires numbers')
    operators={'eq':lambda:value==expected,'ne':lambda:value!=expected,'gt':lambda:value>expected,'gte':lambda:value>=expected,'lt':lambda:value<expected,'lte':lambda:value<=expected}
    if op not in operators:raise ValueError('Unknown condition operator')
    return operators[op]()


from .workflows_execution import ExecutionMixin, retry_policy, output_data, materialize_artifact, spill_output, mask_secrets, run_name, validate_operating_policies, process_identity, sanitize_result


class WorkflowService(ExecutionMixin):
    def __init__(self,store):
        self.store=store
        self._threads={}

    def templates(self):
        with self.store.transaction() as tx:
            demo=next((c for c in tx.all('connection') if c.get('builtin')=='synthetic-orders'),None)
        if not demo:
            # Serialize the absent-row bootstrap through the store transaction lock.
            with self.store.lock:
                with self.store.transaction() as tx:demo=next((c for c in tx.all('connection') if c.get('builtin')=='synthetic-orders'),None)
                if not demo:
                    record=create_connection(self.store,{'name':'Synthetic orders (SQLite)','dialect':'sqlite','config':{'synthetic':True}})
                    with self.store.transaction() as tx:
                        demo=need(tx,'connection',record['id']);demo['builtin']='synthetic-orders';tx.put('connection',demo)
        python='from decimal import Decimal\n\ndef main(inputs):\n    rows = inputs["orders"]\n    total = sum((Decimal(row["amount"]) for row in rows), Decimal("0.00"))\n    return {"summary": {"count": len(rows), "total": format(total, ".2f")}}\n'
        js='const fs = require("fs");\nconst path = require("path");\nconst inputs = JSON.parse(fs.readFileSync(process.env.SLEEP_IN_INPUT_FILE,"utf8"));\nconst message = `${inputs.summary.count} orders • ${inputs.summary.total}`;\nfs.writeFileSync(path.join(process.env.SLEEP_IN_ARTIFACT_DIR,"report.txt"),message+"\\n");\nfs.writeFileSync(process.env.SLEEP_IN_OUTPUT_FILE,JSON.stringify({schemaVersion:1,data:{message},artifacts:[{name:"report.txt",path:"report.txt",mediaType:"text/plain"}]}));\n'
        return [{'id':'morning-report','name':'Morning report','description':'Synthetic SQLite orders → Python summary → JavaScript report. No external account required.','nodes':[
            {'id':'orders','name':'Order query','kind':'sql','source':'SELECT order_id,amount,region FROM orders ORDER BY order_id','config':{'dialect':'sqlite','connection_id':demo['id']},'inputs':{},'outputs':{'type':'object','required':['rows'],'properties':{'rows':{'type':'array'}}},'position':{'x':100,'y':180}},
            {'id':'summary','name':'Order summary','kind':'python','source':python,'config':{},'inputs':{'orders':{'source':'node','node_id':'orders','path':['rows'],'type':'array'}},'outputs':{'type':'object','required':['summary'],'properties':{'summary':{'type':'object','required':['count','total'],'properties':{'count':{'type':'integer'},'total':{'type':'string'}}}}},'position':{'x':420,'y':180}},
            {'id':'report','name':'Report file','kind':'javascript','source':js,'config':{'entry_mode':'file'},'inputs':{'summary':{'source':'node','node_id':'summary','path':['summary'],'type':'object'}},'outputs':{'type':'object','required':['message'],'properties':{'message':{'type':'string'}}},'position':{'x':740,'y':180}}
        ],'edges':[{'source':'orders','target':'summary'},{'source':'summary','target':'report'}],'params':{},'schedule':{'kind':'manual'},'timezone':'UTC','enabled':False,'timeout':600}]

    def validate(self,wf,publication=False):
        errors=[]
        def error(message,node_id=None,field=None,code='workflow_invalid'):errors.append(WorkflowError(message,code,node_id=node_id,field=field).detail)
        nodes=wf.get('nodes',[]);edges=wf.get('edges',[])
        if not isinstance(nodes,list) or not isinstance(edges,list):return [WorkflowError('Nodes and edges must be arrays').detail]
        if not nodes:error('Add at least one node')
        if len(nodes)>50:error('Initial graph limit is 50 nodes')
        ids=[n.get('id') for n in nodes if isinstance(n,dict)]
        if len(ids)!=len(nodes) or any(not isinstance(i,str) or not IDENTIFIER.fullmatch(i) for i in ids):error('Nodes require valid stable IDs');return errors
        if len(ids)!=len(set(ids)):
            error('Duplicate node ID: '+next(i for i in ids if ids.count(i)>1),next(i for i in ids if ids.count(i)>1));return errors
        for edge in edges:
            if not isinstance(edge,dict):error('Edge must be an object');return errors
            for endpoint in ['source','target']:
                if edge.get(endpoint) not in ids:
                    missing=edge.get(endpoint)
                    error('Edge references a missing node: '+str(missing),missing if isinstance(missing,str) else None);return errors
        try:upstream=ancestors(nodes,edges)
        except WorkflowError as exc:return [exc.detail]
        profiles={r['language']:r for r in runtimes()} if publication else {}
        for edge in edges:
            if 'required' in edge and type(edge['required']) is not bool:error('Edge required must be boolean',edge['target'])
            if edge.get('condition'):
                condition=edge['condition']
                if not isinstance(condition,dict) or not isinstance(condition.get('operator'),str) or condition.get('operator') not in {'eq','ne','gt','gte','lt','lte','truthy'}:error('Unknown condition operator',edge['source'])
                else:
                    unsupported=set(condition)-{'path','operator','value'}
                    if unsupported:error('Unsupported condition fields: '+','.join(sorted(unsupported)),edge['source'])
                    try:path_tokens(condition.get('path'))
                    except ValueError as exc:error(str(exc),edge['source'])
        for node in nodes:
            nid=node['id'];kind=node.get('kind');config=node.get('config',{})
            if not isinstance(kind,str):error('Choose a supported language',nid);continue
            if kind not in LANGUAGES and not (kind=='custom' and config.get('runtime_version_id')):error('Choose a supported language',nid)
            if not isinstance(config,dict):error('Node configuration must be an object',nid);continue
            if config.get('migration_requires_runtime') and not (config.get('runtime_version_id') or wf.get('settings',{}).get('runtime_defaults',{}).get(kind)):error('Migrated dependencies require an explicit runtime version',nid)
            incoming=[e for e in edges if e['target']==nid]
            if len(incoming)>1 and config.get('join') not in ('all','any'):error('Multiple predecessors require an explicit join',nid)
            try:retry_policy(node)
            except ValueError as exc:error(str(exc),nid)
            if config.get('join','all') not in ('all','any'):error('Unknown join policy',nid)
            if config.get('merge','named') not in ('named','append'):error('Unknown merge mode',nid)
            if not config.get('project_id') and (not isinstance(node.get('source',''),str) or not node.get('source','').strip()):error('Source is required',nid)
            for schema_key in ['outputs','input_schema']:
                schema=node.get(schema_key,{})
                try:validate_schema(schema)
                except ValueError as exc:
                    # Locate the named property while retaining the schema validator's diagnostic.
                    def invalid_field(value,prefix=''):
                        if not isinstance(value,dict):return prefix
                        for key,child in value.get('properties',{}).items() if isinstance(value.get('properties',{}),dict) else []:
                            try:validate_schema(child)
                            except ValueError:return invalid_field(child,prefix+'.'+key if prefix else key)
                        if 'items' in value:
                            try:validate_schema(value['items'])
                            except ValueError:return invalid_field(value['items'],prefix)
                        return prefix
                    error(str(exc),nid,invalid_field(schema) or schema_key)
            mappings=node.get('inputs',{})
            if not isinstance(mappings,dict):error('Inputs must be a named object',nid);continue
            for field,binding in mappings.items():
                if not isinstance(binding,dict):error('Input binding must be an object',nid,field);continue
                source=binding.get('source')
                if not isinstance(source,str) or source not in {'constant','parameter','node','none','context','artifact','credential'}:
                    error('Unsupported input source',nid,field);continue
                if source=='credential':
                    with self.store.transaction() as tx:credential=tx.get('workflow_credential',binding.get('credential_id',''))
                    if not credential:error('Choose a credential reference',nid,field)
                    elif credential.get('allowed_workflows') and wf.get('id') not in credential['allowed_workflows']:error('Credential not authorized for workflow',nid,field)
                if source=='artifact' and not isinstance(binding.get('name'),str):error('Choose an artifact name',nid,field)
                if source in {'node','artifact'}:
                    if binding.get('node_id') not in ids:error('Input source node is missing: '+str(binding.get('node_id')),nid,field,code='missing_source')
                    elif binding.get('node_id') not in upstream[nid]:error('Input source must be a reachable upstream node',nid,field,code='unreachable_source')
                if binding.get('optional') and 'default' not in binding:error('Optional input requires an explicit default',nid,field)
                if source=='node':
                    parent=next((n for n in nodes if n['id']==binding.get('node_id')),None)
                    schema=parent.get('outputs',{}) if parent else {}
                    try:
                        validate_schema(schema)
                        validate_schema(node.get('input_schema',{}))
                        for token in path_tokens(binding.get('path')):
                            if not isinstance(schema,dict):schema={};break
                            types=schema.get('type',[]);types=types if isinstance(types,list) else [types]
                            if types and not any(t in {'object','array'} for t in types):
                                raise ValueError('Input source path cannot traverse declared '+','.join(types)+' schema')
                            containers=set(types)&{'object','array'}
                            if containers=={'array'}:
                                if not (type(token) is int or isinstance(binding.get('path'),str) and isinstance(token,str) and token.isdigit()):
                                    raise ValueError('Input source path requires an array index')
                                schema=schema.get('items',{})
                            elif containers=={'object'} or not types:
                                if type(token) is int and containers=={'object'}:raise ValueError('Input source path requires an object key')
                                schema=schema.get('properties',{}).get(token,{})
                            else:schema={}
                        offered=schema.get('type') if isinstance(schema,dict) else None
                        target=node.get('input_schema',{})
                        target=target.get('properties',{}).get(field,{}) if isinstance(target,dict) else {}
                        for requested in [binding.get('type'),target.get('type') if isinstance(target,dict) else None]:
                            if requested is None:continue
                            available=offered if isinstance(offered,list) else [offered] if offered else []
                            accepted=requested if isinstance(requested,list) else [requested]
                            if any(t not in accepted and not (t=='integer' and 'number' in accepted) for t in available):
                                error('Upstream and input types are incompatible: '+str(offered)+' -> '+str(requested),nid,field)
                    except ValueError as exc:error(str(exc),nid,field)
                try:
                    path_tokens(binding.get('path'))
                    if 'type' in binding:validate_schema({'type':binding['type']})
                    if 'default' in binding and 'type' in binding:check_schema(binding['default'],{'type':binding['type']},field)
                except ValueError as exc:error(str(exc),nid,field)
            if publication and not (config.get('runtime_version_id') or wf.get('settings',{}).get('runtime_defaults',{}).get(kind)) and kind in profiles and profiles[kind]['status']!='ready':error(profiles[kind]['reason'],nid)
            if kind=='sql':
                from .workflows_sql import driver_status
                with self.store.transaction() as tx:connection=tx.get('connection',config.get('connection_id',''))
                if not connection:error('Choose a database connection',nid)
                elif publication and not driver_status(connection['dialect']):error('Install the '+connection['dialect']+' driver before publishing',nid)
                elif connection.get('allowed_workflows') and wf.get('id') not in connection['allowed_workflows']:error('Connection not authorized for workflow',nid)
                elif connection['dialect']!=config.get('dialect','sqlite'):error('SQL dialect and connection differ',nid)
                elif config.get('mode')=='write' and not connection.get('write_enabled'):error('Connection does not permit writes',nid)
        try:workflow_next_runs(wf.get('schedule',{'kind':'manual'}),wf.get('timezone','UTC'),now(),count=1)
        except (ValueError,KeyError,TypeError) as exc:error(str(exc))
        return errors

    def save(self,data,wid=None):
        if not isinstance(data,dict):raise WorkflowError('Workflow must be an object')
        with self.store.transaction() as tx:old=need(tx,'workflow',wid) if wid else {}
        wf={**old,**{k:copy.deepcopy(v) for k,v in data.items() if k in {'name','description','nodes','edges','params','parameter_schema','schedule','timezone','enabled','timeout','triggers','allowed_user_ids','settings','notifications','retention','run_name_template'}}}
        wf.update(id=wid or uid(),updated_at=stamp())
        for key,value in {'name':'Untitled workflow','description':'','nodes':[],'edges':[],'params':{},'schedule':{'kind':'manual'},'timezone':'UTC','enabled':False,'timeout':600,'created_at':stamp(),'published_version_id':None}.items():wf.setdefault(key,value)
        if not wf.get('name'):raise WorkflowError('Workflow name is required')
        wf['schedule']=normalize_workflow_schedule(wf['schedule'],wf['timezone'])
        if wf.get('schedule',{}).get('kind')=='cron':raise WorkflowError('Choose a form schedule; Cron is unsupported')
        validate_operating_policies(self.store,wf)
        triggers=wf.get('triggers',[])
        if not isinstance(triggers,list):raise WorkflowError('Triggers must be an array')
        ids=set()
        for trigger in triggers:
            if not isinstance(trigger,dict) or not isinstance(trigger.get('id'),str) or not IDENTIFIER.fullmatch(trigger['id']) or trigger['id'] in ids:raise WorkflowError('Triggers require unique stable IDs')
            ids.add(trigger['id'])
            if trigger.get('overlap','skip') not in {'skip','queue'}:raise WorkflowError('Unknown overlap policy')
            if type(trigger.get('queue_limit',10)) is not int or not 1<=trigger.get('queue_limit',10)<=100:raise WorkflowError('Queue limit must be 1..100')
            if trigger.get('missed_policy','skip') not in {'skip','latest_once'}:raise WorkflowError('Unknown missed occurrence policy')
            if type(trigger.get('grace_seconds',7200)) is not int or not 1<=trigger.get('grace_seconds',7200)<=86400:raise WorkflowError('Grace seconds must be 1..86400')
            validate_schema(trigger.get('parameter_schema',{}))
            if trigger.get('kind') not in {'manual','api','scheduled'}:raise WorkflowError('Unknown trigger kind')
            if 'schedule' in trigger:trigger['schedule']=normalize_workflow_schedule(trigger['schedule'],trigger.get('timezone',wf['timezone']))
            if trigger.get('schedule',{}).get('kind')=='cron':raise WorkflowError('Choose a form schedule; Cron is unsupported')
            if not isinstance(trigger.get('params',{}),dict):raise WorkflowError('Trigger parameters must be an object')
            if trigger.get('enabled') and not wf.get('published_version_id'):raise WorkflowError('Publish before enabling triggers')
            if trigger.get('enabled') and trigger['kind']=='scheduled':workflow_next_runs(trigger.get('schedule',{}),trigger.get('timezone',wf['timezone']),now(),count=1)
            if trigger.get('version_id'):
                with self.store.transaction() as tx:version=need(tx,'workflow_version',trigger['version_id'])
                if version['workflow_id']!=wf['id']:raise WorkflowError('Pinned publication belongs to another workflow')
        if wf.get('migration') and not wf['migration'].get('handoff_complete') and (wf.get('enabled') or any(t.get('enabled') for t in triggers)):raise WorkflowError('Migration handoff required before enabling triggers')
        if wf.get('enabled') and not wf.get('published_version_id'):raise WorkflowError('Publish before enabling schedules')
        if wf.get('enabled'):workflow_next_runs(wf['schedule'],wf['timezone'],now(),count=1)
        wf['validation_errors']=self.validate(wf)
        with self.store.transaction() as tx:tx.put('workflow',wf)
        return copy.deepcopy(wf)

    def publish(self,wid):
        with self.store.transaction() as tx:wf=need(tx,'workflow',wid)
        validate_operating_policies(self.store,wf)
        errors=self.validate(wf,True)
        if errors:raise WorkflowError(errors[0]['message'],errors[0]['code'],node_id=errors[0].get('node_id'),field=errors[0].get('field'))
        from .workflows_packs import prepare_node
        snapshot=copy.deepcopy(wf)
        for index,node in enumerate(snapshot['nodes']):
            default=snapshot.get('settings',{}).get('runtime_defaults',{}).get(node['kind'])
            if default:node.setdefault('config',{}).setdefault('runtime_version_id',default)
            try:snapshot['nodes'][index]=prepare_node(self.store,node)
            except (ValueError,TimeoutError) as exc:raise WorkflowError(str(exc),node_id=node['id'])
        with self.store.transaction() as tx:
            current=need(tx,'workflow',wid)
            if current['updated_at']!=wf['updated_at']:raise WorkflowError('Draft changed during publication; retry')
            number=1+len([v for v in tx.all('workflow_version') if v['workflow_id']==wid])
            version={'id':uid(),'workflow_id':wid,'number':number,'created_at':stamp(),'snapshot':snapshot,'digest':hashlib.sha256(json.dumps(snapshot,sort_keys=True).encode()).hexdigest()}
            tx.put('workflow_version',version);current['published_version_id']=version['id'];current['published_version']=number;tx.put('workflow',current)
        return {'version_id':version['id'],'version':number,'workflow':current}

    def resolve_admission(self,wid,key):
        """Resolve or retire one manual request atomically against admission."""
        if not isinstance(key,str) or not key:
            raise WorkflowError('Provide a nonempty idempotency key','workflow_invalid',field='idempotency_key')
        with self.store.transaction() as tx:
            need(tx,'workflow',wid)
            previous=next((r for r in tx.all('workflow_run') if r['workflow_id']==wid and r.get('idempotency_key')==key and r.get('trigger_id')=='manual'),None)
            if previous:return {'status':'admitted','run':public_run(previous)}
            rid=admission_retirement_id(wid,key)
            if not tx.get('workflow_admission_retirement',rid):
                tx.put('workflow_admission_retirement',{'id':rid,'workflow_id':wid,'trigger_id':'manual','idempotency_key':key,'retired_at':stamp()})
            return {'status':'retired','idempotency_key':key}

    def admit(self,wid,params=None,key=None,test=False,trigger_id='manual',version_id=None,prepared_test=None,overlap='skip',queue_limit=10,scheduled_at=None):
        if (self.store.path/'local-stop-request.json').exists():raise WorkflowError('Background service is draining; new admission is paused','service_stopping')
        prepared=None
        if test:
            with self.store.transaction() as tx:prepared=copy.deepcopy(prepared_test) if prepared_test is not None else need(tx,'workflow',wid)
            errors=self.validate(prepared,True)
            if errors:raise WorkflowError(errors[0]['message'])
            from .workflows_packs import prepare_node
            for node in prepared['nodes']:
                default=prepared.get('settings',{}).get('runtime_defaults',{}).get(node['kind'])
                if default:node.setdefault('config',{}).setdefault('runtime_version_id',default)
            prepared['nodes']=[prepare_node(self.store,node) for node in prepared['nodes']]
        with self.store.transaction() as tx:
            wf=need(tx,'workflow',wid)
            if key:
                previous=next((r for r in tx.all('workflow_run') if r['workflow_id']==wid and r.get('idempotency_key')==key and r.get('trigger_id')==trigger_id),None)
                if previous:return previous
                if trigger_id=='manual' and tx.get('workflow_admission_retirement',admission_retirement_id(wid,key)):
                    raise WorkflowError('This admission key was retired; use a new key for a deliberate new run','admission_retired')
            maintenance=tx.get('meta','workflow_maintenance') or {}
            if wf.get('migration') and not wf['migration'].get('handoff_complete') and not test:raise WorkflowError('Migration handoff required before production admission')
            if maintenance.get('paused') and not test:raise WorkflowError('Workflow admission paused for maintenance','service_stopping')
            active=[r for r in tx.all('workflow_run') if r['workflow_id']==wid and r['status'] in ACTIVE]
            if active and (overlap!='queue' or len(active)>=queue_limit+1):raise WorkflowError('Workflow already has an active run or queue is full','workflow_active')
            if test:
                if prepared['updated_at']!=wf['updated_at']:raise WorkflowError('Draft changed during test admission; retry')
                snapshot=prepared
                vid=None
            else:
                vid=version_id or wf.get('published_version_id')
                if not vid:raise WorkflowError('Publish the workflow before running')
                version=need(tx,'workflow_version',vid)
                if version['workflow_id']!=wid:raise WorkflowError('Publication belongs to another workflow')
                if version.get('restore_requires_rebuild'):raise WorkflowError('Restored publication requires runtime rebuild and republish')
                snapshot=version['snapshot']
            values=copy.deepcopy(snapshot.get('params',{}));values.update(copy.deepcopy(params or {}))
            check_schema(values,snapshot.get('parameter_schema',{}),'params');portable(values)
            run={'id':uid(),'workflow_id':wid,'workflow_name':wf['name'],'version_id':vid,'snapshot':copy.deepcopy(snapshot),'status':'queued','params':values,'test':test,'test_node_id':snapshot.get('test_node_id'),'trigger_id':trigger_id,'idempotency_key':key,'created_at':stamp(),'started_at':None,'finished_at':None,'nodes':{n['id']:{'status':'queued','attempts':[]} for n in snapshot['nodes']},'artifacts':[],'error':None,'engine':'n8n','callback_token':secrets.token_urlsafe(32),'timeout':min(max(int(snapshot.get('timeout',600)),1),86400)}
            run['name']=run_name(snapshot,values)
            run['scheduled_at']=scheduled_at
            if test and snapshot.get('test_input_source'):run['test_input_source']=copy.deepcopy(snapshot['test_input_source'])
            tx.put('workflow_run',run)
        return run

    def get_run(self,rid):
        with self.store.transaction() as tx:return need(tx,'workflow_run',rid)

    def _cancelled(self,rid):
        with self.store.transaction() as tx:
            run=need(tx,'workflow_run',rid)
            if run['status']=='running' and run.get('started_at') and (now()-datetime.fromisoformat(run['started_at'])).total_seconds()>run['timeout']:
                run.update(status='timed_out',error='Workflow deadline exceeded',finished_at=stamp())
                for state in run['nodes'].values():
                    if state['status'] in {'queued','dispatching'}:state.update(status='not_run',reason='workflow_timed_out',finished_at=stamp())
                tx.put('workflow_run',run)
            return run['status'] in {'cancelling','cancelled','timed_out','failed'}

    def execute_node(self,rid,nid):
        with self.store.transaction() as tx:
            run=need(tx,'workflow_run',rid)
            if nid not in run['nodes']:raise WorkflowError('Node is not in this run')
            state=run['nodes'][nid]
            if state['status'] in NODE_TERMINAL or state['status']=='running':return copy.deepcopy(state)
            if run['status'] in {'cancelled','cancelling','timed_out','failed','succeeded','partial'}:
                state.update(status='cancelled' if run['status'] in {'cancelled','cancelling'} else 'not_run',finished_at=stamp());tx.put('workflow_run',run);return state
            snapshot=run['snapshot'];node=copy.deepcopy(next(n for n in snapshot['nodes'] if n['id']==nid))
            attempt=len(state['attempts'])+1
            directory=self.store.path/'workflow-runs'/rid/nid/('attempt-'+str(attempt))
            incoming=[e for e in snapshot['edges'] if e['target']==nid]
            if any(run['nodes'][e['source']]['status'] not in NODE_TERMINAL for e in incoming):raise WorkflowError('Predecessors are not terminal','dependencies_pending',nid)
            state.update(started_at=stamp(),inputs={},input_provenance={},stdout='',stderr='')
            for transient in ('error','error_detail','reason','finished_at'):
                state.pop(transient,None)
            try:
                successful=[];required_failure=False
                for edge in incoming:
                    upstream=run['nodes'][edge['source']]
                    if upstream['status']=='succeeded':
                        try:active=not edge.get('condition') or condition_matches(output_data(self.store,run,upstream),edge['condition'])
                        except (ValueError,KeyError,IndexError,TypeError) as exc:raise WorkflowError('Condition: '+str(exc),node_id=nid)
                        if active:successful.append(edge['source'])
                    elif upstream['status'] not in {'skipped'} and edge.get('required',True):required_failure=True
                if required_failure:
                    state.update(status='not_run',reason='blocked_by_failed_dependency',finished_at=stamp());tx.put('workflow_run',run);return copy.deepcopy(state)
                if incoming and not successful:
                    all_skipped=all(run['nodes'][e['source']]['status'] in {'succeeded','skipped'} for e in incoming)
                    state.update(status='skipped' if all_skipped else 'not_run',reason='unselected_branch' if all_skipped else 'no_successful_dependency',finished_at=stamp());tx.put('workflow_run',run);return copy.deepcopy(state)
                inputs={};secret_values=[];credential_revisions={}
                if node['kind']=='sql':
                    connection=need(tx,'connection',node.get('config',{}).get('connection_id',''))
                    if connection.get('allowed_workflows') and run['workflow_id'] not in connection['allowed_workflows']:raise WorkflowError('Connection not authorized for workflow',node_id=nid)
                    def collect_connection_values(value):
                        if isinstance(value,str) and value:secret_values.append(value)
                        elif isinstance(value,dict):
                            for item in value.values():collect_connection_values(item)
                        elif isinstance(value,list):
                            for item in value:collect_connection_values(item)
                    collect_connection_values(json.loads(self.store.fernet.decrypt(connection['encrypted_config'].encode())))
                for field,binding in node.get('inputs',{}).items():
                    source=binding.get('source')
                    if source=='none':continue
                    provenance={'source':source,'default_used':False}
                    if source in {'node','artifact'}:provenance['node_id']=binding.get('node_id')
                    if source in {'node','parameter','context'}:provenance['path']=copy.deepcopy(binding.get('path',[]))
                    try:
                        if source=='constant':value=copy.deepcopy(binding.get('value'))
                        elif source=='parameter':value=resolve_path(run['params'],binding.get('path'))
                        elif source=='credential':
                            credential=need(tx,'workflow_credential',binding.get('credential_id',''))
                            if credential.get('allowed_workflows') and run['workflow_id'] not in credential['allowed_workflows']:raise WorkflowError('Credential not authorized for workflow',node_id=nid,field=field)
                            value=self.store.fernet.decrypt(credential['encrypted'].encode()).decode();secret_values.append(value);credential_revisions[credential['id']]=credential['revision']
                        elif source=='context':value=resolve_path({'run_id':rid,'workflow_id':run['workflow_id'],'version_id':run['version_id']},binding.get('path'))
                        elif source in {'node','artifact'}:
                            parent=run['nodes'][binding['node_id']]
                            if parent['status']!='succeeded':raise KeyError('source unavailable')
                            # A direct condition-false edge must not expose an unselected output.
                            direct=[e for e in incoming if e['source']==binding['node_id']]
                            if direct and binding['node_id'] not in successful:raise KeyError('source unselected')
                            value=materialize_artifact(self.store,run,binding,directory) if source=='artifact' else resolve_path(output_data(self.store,run,parent),binding.get('path'))
                        else:raise ValueError('Unsupported input source')
                    except (KeyError,IndexError):
                        if binding.get('optional') and 'default' in binding:
                            value=copy.deepcopy(binding['default']);provenance['default_used']=True
                        else:
                            origin=binding.get('node_id',source)
                            raise WorkflowError('Missing required input '+nid+'.'+field+' from '+str(origin)+' path '+json.dumps(binding.get('path',[])),node_id=nid,field=field)
                    if 'type' in binding:check_schema(value,{'type':binding['type']},field)
                    inputs[field]=copy.deepcopy(value)
                    state['input_provenance'][field]=provenance
                if node.get('config',{}).get('merge')=='append':
                    if any(not isinstance(v,list) for v in inputs.values()):raise WorkflowError('Append merge requires array inputs',node_id=nid)
                    inputs={node['config'].get('merge_target','items'):[item for values in inputs.values() for item in values]}
                check_schema(inputs,node.get('input_schema',{}),'inputs');portable(inputs)
                state.update(status='running',inputs=mask_secrets(inputs,secret_values),credential_revisions=credential_revisions,process_started=False)
                run['status']='running';run['started_at']=run['started_at'] or stamp()
                state['attempts'].append({'number':attempt,'status':'running','started_at':stamp()})
                tx.put('workflow_run',run)
            except (ValueError,KeyError,TypeError) as exc:
                state.update(status='failed',error=str(exc),finished_at=stamp())
                if isinstance(exc,WorkflowError):state['error_detail']=exc.detail
                tx.put('workflow_run',run);return copy.deepcopy(state)
        node['_execution']={'run_id':rid,'node_id':nid}
        def worker_started(pid):
            identity=process_identity(pid)
            with self.store.transaction() as tx:
                current=need(tx,'workflow_run',rid);current['nodes'][nid].update(worker_pid=pid,worker_identity=identity);tx.put('workflow_run',current)
        def worker_exited():
            with self.store.transaction() as tx:
                current=need(tx,'workflow_run',rid);current['nodes'][nid].update(worker_pid=None,worker_identity=None);tx.put('workflow_run',current)
        node['_on_process']=worker_started;node['_on_process_exit']=worker_exited
        result=None
        try:
            with self.store.transaction() as tx:
                current=need(tx,'workflow_run',rid);current['nodes'][nid]['process_started']=True;tx.put('workflow_run',current)
            if node['kind']=='sql':result=execute_sql(self.store,node,inputs,run['workflow_id'],lambda:self._cancelled(rid))
            else:result=run_script(node,inputs,directory,self.store.path,lambda:self._cancelled(rid))
            if result['status']=='succeeded':
                check_schema(result['output']['data'],node.get('outputs',{}))
                result=sanitize_result(result,secret_values)
                spill_output(result,directory)
        except Exception as exc:
            logs=exc.logs if isinstance(exc,WorkerOutputError) else {key:(result or {}).get(key,'') for key in ('stdout','stderr')}
            result={'status':'failed','error':str(exc),**logs}
        if result['status']!='succeeded':result=sanitize_result(result,secret_values)
        with self.store.transaction() as tx:
            current=need(tx,'workflow_run',rid);state=current['nodes'][nid]
            if current['status'] in {'cancelling','cancelled'}:result.update(status='cancelled',error='Cancelled')
            if current['status']=='timed_out':result.update(status='timed_out',error='Workflow deadline exceeded')
            state.update(result,finished_at=stamp())
            state['attempts'][-1].update(status=result['status'],finished_at=state['finished_at'],error=result.get('error'),stdout=result.get('stdout',''),stderr=result.get('stderr',''))
            for artifact in state.get('output',{}).get('artifacts',[]):
                artifact.update(id=uid(),node_id=nid);current['artifacts'].append(copy.deepcopy(artifact))
            tx.put('workflow_run',current)
        return copy.deepcopy(state)

    def finish(self,rid):
        with self.store.transaction() as tx:
            run=need(tx,'workflow_run',rid)
            if run['status'] not in ACTIVE:return run
            pending=[nid for nid,n in run['nodes'].items() if n['status'] not in NODE_TERMINAL]
            if any(n['status'] in {'running','dispatching'} for n in run['nodes'].values()):raise WorkflowError('Nodes still running','dependencies_pending',pending=pending)
            if run['status']=='cancelling':
                for n in run['nodes'].values():
                    if n['status']=='queued':n.update(status='cancelled',finished_at=stamp())
                run['status']='cancelled'
            elif any(n['status']=='queued' for n in run['nodes'].values()):raise WorkflowError('Nodes are not terminal','dependencies_pending',pending=pending)
            else:
                states=run['nodes'];failed=[nid for nid,n in states.items() if n['status'] in {'failed','timed_out','not_run','cancelled'}]
                tolerated=all(any(e['source']==nid for e in run['snapshot']['edges']) and all(not e.get('required',True) for e in run['snapshot']['edges'] if e['source']==nid) for nid in failed)
                if any(n['status']=='timed_out' for n in states.values()):run['status']='timed_out'
                elif failed:run['status']='partial' if tolerated and any(n['status']=='succeeded' for n in states.values()) else 'failed'
                else:run['status']='succeeded'
            run['finished_at']=stamp();tx.put('workflow_run',run)
        self.on_terminal(rid)
        return run

    def cancel(self,rid):
        with self.store.transaction() as tx:
            run=need(tx,'workflow_run',rid)
            if run['status'] not in ACTIVE:return run
            running=any(n['status'] in {'running','dispatching'} for n in run['nodes'].values())
            run['status']='cancelling' if running else 'cancelled'
            if not running:
                for n in run['nodes'].values():
                    if n['status']=='queued':n.update(status='cancelled',finished_at=stamp())
                run['finished_at']=stamp()
            tx.put('workflow_run',run)
        self.on_terminal(rid)
        return run

    def on_terminal(self,rid):
        from .workflows_operations import WorkflowOperations
        try:WorkflowOperations(self.store).on_run_terminal(rid)
        except Exception as exc:
            with self.store.transaction() as tx:tx.put('workflow_event',{'id':uid(),'run_id':rid,'reason':'terminal_notification_enqueue_failed','created_at':stamp()})

    def tick(self,moment=None):
        moment=moment or now()
        if (self.store.path/'local-stop-request.json').exists():return []
        admitted=[]
        with self.store.transaction() as tx:workflows=tx.all('workflow')
        for wf in workflows:
            if not wf.get('published_version_id'):continue
            triggers=copy.deepcopy(wf.get('triggers',[]))
            if wf.get('enabled') and wf.get('schedule',{}).get('kind')!='manual':triggers.append({'id':'default','kind':'scheduled','enabled':True,'schedule':wf['schedule'],'timezone':wf['timezone'],'params':{}})
            for trigger in triggers:
                if not trigger.get('enabled') or trigger.get('kind') not in {'scheduled','schedule'}:continue
                tid=trigger['id'];cursor_id=wf['id']+':'+tid
                with self.store.transaction() as tx:
                    cursor=tx.get('workflow_cursor',cursor_id)
                    previous=datetime.fromisoformat(cursor['last_tick']) if cursor else moment
                    since=max(previous,moment-timedelta(seconds=trigger.get('grace_seconds',7200) if trigger.get('missed_policy')=='latest_once' else 30))
                    if previous<moment-timedelta(seconds=30):
                        tx.put('workflow_event',{'id':uid(),'workflow_id':wf['id'],'trigger_id':tid,'reason':'offline_gap_latest_once' if trigger.get('missed_policy')=='latest_once' else 'offline_gap_skipped','from':previous.isoformat(),'until':since.isoformat(),'created_at':stamp()})
                    occurrences=workflow_next_runs(trigger['schedule'],trigger.get('timezone',wf['timezone']),since,count=1441 if trigger.get('missed_policy')=='latest_once' else 1)
                    due=next((d for d in reversed(occurrences) if d<=moment),None)
                    # Cursor and pending intent commit together. Admission may be retried by key.
                    if due:
                        oid=hashlib.sha256((cursor_id+':'+due.isoformat()).encode()).hexdigest()
                        if not tx.get('workflow_occurrence',oid):
                            tx.put('workflow_occurrence',{'id':oid,'workflow_id':wf['id'],'trigger_id':tid,'occurrence':due.isoformat(),'params':trigger.get('params',{}),'version_id':trigger.get('version_id') or wf['published_version_id'],'status':'pending','attempts':0,'created_at':stamp()})
                    tx.put('workflow_cursor',{'id':cursor_id,'last_tick':moment.isoformat()})
        with self.store.transaction() as tx:pending=tx.all('workflow_occurrence')
        for item in pending:
            claim=uid()
            with self.store.transaction() as tx:
                item=need(tx,'workflow_occurrence',item['id'])
                wf=need(tx,'workflow',item['workflow_id'])
                trigger=next((t for t in wf.get('triggers',[]) if t['id']==item['trigger_id']),None)
                if item['trigger_id']=='default':trigger={'enabled':wf.get('enabled',False)}
                if not trigger or not trigger.get('enabled'):
                    if item['status'] in {'pending','admitting'}:
                        item.update(status='skipped',reason='trigger_disabled');tx.put('workflow_occurrence',item)
                    continue
                recoverable=item['status']=='admitting' and datetime.fromisoformat(item['claimed_at'])<moment-timedelta(seconds=30)
                if item['status']!='pending' and not recoverable:continue
                item.update(status='admitting',claim=claim,claimed_at=moment.isoformat(),attempts=item.get('attempts',0)+1);tx.put('workflow_occurrence',item)
            try:
                run=self.admit(item['workflow_id'],item['params'],key='schedule:'+item['occurrence'],trigger_id=item['trigger_id'],version_id=item['version_id'],overlap=trigger.get('overlap','skip'),queue_limit=trigger.get('queue_limit',10),scheduled_at=item['occurrence'])
                result={'status':'admitted','run_id':run['id'],'error':None}
                admitted.append(run)
            except Exception as exc:
                overlap=isinstance(exc,WorkflowError) and exc.detail['code']=='workflow_active'
                result={'status':'skipped' if overlap else 'pending','error':str(exc),'reason':'overlap_skipped' if overlap else 'admission_retry_pending'}
            with self.store.transaction() as tx:
                current=need(tx,'workflow_occurrence',item['id'])
                if current.get('claim')==claim:
                    current.update(result);tx.put('workflow_occurrence',current)
                    if result['status']=='skipped':tx.put('workflow_event',{'id':item['id'],'workflow_id':item['workflow_id'],'trigger_id':item['trigger_id'],'reason':'overlap_skipped','occurrence':item['occurrence'],'created_at':stamp()})
        return admitted

    def dispatch_pending(self):
        from .workflows_n8n import dispatch_pending
        return dispatch_pending(self)


def public_run(run):
    value=copy.deepcopy(run)
    for node in run.get('snapshot',{}).get('nodes',[]):
        if node['id'] in value.get('nodes',{}):value['nodes'][node['id']].update(name=node.get('name',node['id']),kind=node['kind'])
    for key in ('snapshot','callback_token','adapter_lease','adapter_pid','adapter_execution_owner'):value.pop(key,None)
    for artifact in value.get('artifacts',[]):artifact.pop('path',None)
    for node in value.get('nodes',{}).values():
        if node.get('output',{}).get('data_ref'):node['output']['data_ref'].pop('path',None)
        for artifact in node.get('output',{}).get('artifacts',[]):artifact.pop('path',None)
    return value


def register_workflow_routes(app,store,require):
    service=WorkflowService(store)
    app.state.workflows=service

    @app.exception_handler(WorkflowError)
    async def workflow_error(request,exc):
        return JSONResponse({'detail':exc.detail},status_code=404 if exc.detail['code']=='not_found' else 409 if exc.detail['code'] in {'dependencies_pending','service_stopping','workflow_active'} else 422)

    def auth(request,wid=None,admin=False):
        with store.transaction() as tx:
            user=require(request,tx,admin=admin)
            if wid:
                wf=need(tx,'workflow',wid)
                if user['role']!='admin' and wf.get('allowed_user_ids') and user['id'] not in wf['allowed_user_ids']:raise HTTPException(403,detail={'code':'forbidden','message':'Workflow access required'})
            return user

    def visible_workflow(wf,user):
        value=copy.deepcopy(wf)
        if user['role']!='admin':
            for node in value.get('nodes',[]):node.pop('source',None)
        return value

    @app.get('/api/workflows')
    def list_workflows(request:Request):
        user=auth(request)
        with store.transaction() as tx:values=tx.all('workflow')
        return [visible_workflow(w,user) for w in values if user['role']=='admin' or not w.get('allowed_user_ids') or user['id'] in w['allowed_user_ids']]

    @app.post('/api/workflows')
    def add_workflow(request:Request,body:dict):
        auth(request,admin=True);return service.save(body)

    @app.get('/api/workflows/{wid}')
    def get_workflow(wid:str,request:Request):
        user=auth(request,wid)
        with store.transaction() as tx:return visible_workflow(need(tx,'workflow',wid),user)

    @app.put('/api/workflows/{wid}')
    def put_workflow(wid:str,request:Request,body:dict):
        auth(request,wid,admin=True);return service.save(body,wid)

    @app.post('/api/workflows/{wid}/publish')
    def publish_workflow(wid:str,request:Request):
        auth(request,wid,admin=True);return service.publish(wid)

    @app.post('/api/workflows/{wid}/run',status_code=202)
    def run_workflow(wid:str,request:Request,body:dict):
        auth(request,wid,admin=bool(body.get('test')))
        if set(body)&{'resume','resume_from','resume_from_node','resume_node_id','failed_node'}:
            raise WorkflowError('Resume from a node is unsupported; explicitly admit a separate full run instead','unsupported_resume')
        return public_run(service.admit(wid,body.get('params',{}),key=body.get('idempotency_key') or request.headers.get('idempotency-key'),test=bool(body.get('test')),version_id=body.get('version_id')))

    @app.post('/api/workflows/{wid}/admission-resolution')
    def resolve_workflow_admission(wid:str,request:Request,body:dict):
        auth(request,wid)
        return service.resolve_admission(wid,body.get('idempotency_key'))

    @app.post('/api/workflows/{wid}/nodes/{nid}/test',status_code=202)
    def test_node(wid:str,nid:str,request:Request,body:dict):
        auth(request,wid,admin=True)
        return public_run(service.test_node(wid,nid,body))

    @app.post('/api/workflows/{wid}/triggers/{tid}/run',status_code=202)
    def trigger_run(wid:str,tid:str,request:Request,body:dict):
        auth(request,wid)
        if set(body)&{'resume','resume_from','resume_from_node','resume_node_id','failed_node'}:
            raise WorkflowError('Resume from a node is unsupported; explicitly admit a separate full run instead','unsupported_resume')
        body=dict(body);body.setdefault('idempotency_key',request.headers.get('idempotency-key'))
        return public_run(service.trigger_run(wid,tid,body))

    @app.post('/api/workflows/{wid}/preview')
    def preview_workflow(wid:str,request:Request,body:dict):
        auth(request,wid)
        with store.transaction() as tx:wf=need(tx,'workflow',wid)
        after=datetime.fromisoformat(body['after'].replace('Z','+00:00')) if body.get('after') else now()
        tz=body.get('timezone',wf['timezone'])
        return {'next_runs':[v.isoformat() for v in workflow_next_runs(body.get('schedule',wf['schedule']),tz,after)],'timezone':tz}

    @app.get('/api/workflow-templates')
    def templates(request:Request):
        auth(request);return service.templates()

    @app.get('/api/workflow-runs')
    def list_runs(request:Request,workflow_id:str=None):
        user=auth(request,workflow_id)
        with store.transaction() as tx:
            workflows={w['id']:w for w in tx.all('workflow')};runs=tx.all('workflow_run')
        return [public_run(r) for r in sorted(runs,key=lambda r:r['created_at'],reverse=True) if (not workflow_id or r['workflow_id']==workflow_id) and (user['role']=='admin' or not workflows.get(r['workflow_id'],{}).get('allowed_user_ids') or user['id'] in workflows[r['workflow_id']]['allowed_user_ids'])]

    @app.get('/api/workflow-runs/{rid}')
    def get_run(rid:str,request:Request):
        run=service.get_run(rid);auth(request,run['workflow_id']);return public_run(run)

    @app.get('/api/workflow-runs/{rid}/nodes/{nid}/sample')
    def node_sample(rid:str,nid:str,request:Request):
        run=service.get_run(rid);auth(request,run['workflow_id'])
        state=run['nodes'].get(nid)
        if not state or 'inputs' not in state:raise WorkflowError('Sample unavailable','not_found')
        with store.transaction() as tx:wf=need(tx,'workflow',run['workflow_id'])
        return {'inputs':copy.deepcopy(state['inputs']),'run_id':rid,'version_id':run['version_id'],'produced_at':state.get('finished_at'),'stale':run['version_id']!=wf.get('published_version_id') or run['test'] or run['snapshot'].get('updated_at')!=wf.get('updated_at')}

    @app.get('/api/workflow-runs/{rid}/nodes/{nid}/output')
    def node_output(rid:str,nid:str,request:Request,path:str='',offset:int=0,limit:int=100):
        run=service.get_run(rid);auth(request,run['workflow_id'])
        if nid not in run['nodes'] or 'output' not in run['nodes'][nid]:raise WorkflowError('Output unavailable','not_found')
        if offset<0 or not 1<=limit<=1000:raise WorkflowError('Invalid pagination bounds')
        value=resolve_path(output_data(store,run,run['nodes'][nid]),path)
        total=len(value) if isinstance(value,list) else 1
        page=value[offset:offset+limit] if isinstance(value,list) else value
        if len(json.dumps(page).encode())>1024*1024:raise WorkflowError('Preview too large; select a field or download the complete artifact')
        return {'data':page,'total':total,'offset':offset,'limit':limit,'has_more':isinstance(value,list) and offset+limit<total}

    @app.post('/api/workflow-runs/{rid}/cancel')
    def cancel_run(rid:str,request:Request):
        run=service.get_run(rid);auth(request,run['workflow_id']);return public_run(service.cancel(rid))

    @app.get('/api/workflow-runs/{rid}/artifacts/{aid}')
    def artifact(rid:str,aid:str,request:Request):
        run=service.get_run(rid);auth(request,run['workflow_id'])
        if run.get('data_expired'):raise WorkflowError('Artifact expired under the run retention policy','not_found')
        item=next((a for a in run['artifacts'] if a['id']==aid),None)
        if not item:raise WorkflowError('Artifact not found','not_found')
        file=Path(item['path']).resolve();root=(store.path/'workflow-runs'/rid).resolve()
        if not file.is_relative_to(root) or not file.is_file():raise WorkflowError('Artifact missing or expired','not_found')
        if hashlib.sha256(file.read_bytes()).hexdigest()!=item['sha256']:raise WorkflowError('Artifact checksum changed')
        filename=re.sub(r'[\x00-\x1f\x7f]','_',str(item['name']).replace('\\','/').rsplit('/',1)[-1])
        if filename in {'','.','..'}:filename='artifact'
        return FileResponse(file,filename=filename,media_type=item['mediaType'])

    @app.get('/api/workflow-credentials')
    def credentials(request:Request):
        auth(request,admin=True)
        with store.transaction() as tx:return [{k:v for k,v in c.items() if k!='encrypted'} for c in tx.all('workflow_credential')]

    @app.post('/api/workflow-credentials')
    def add_credential(request:Request,body:dict):
        auth(request,admin=True);return service.save_credential(body)

    @app.put('/api/workflow-credentials/{cid}')
    def update_credential(cid:str,request:Request,body:dict):
        auth(request,admin=True);return service.save_credential(body,cid)

    @app.get('/api/connections')
    def connections(request:Request):
        auth(request)
        with store.transaction() as tx:return [public_connection(c) for c in tx.all('connection')]

    @app.post('/api/connections')
    def add_connection(request:Request,body:dict):
        auth(request,admin=True);return create_connection(store,body)

    @app.put('/api/connections/{cid}')
    def rotate_connection(cid:str,request:Request,body:dict):
        auth(request,admin=True);return update_connection(store,cid,body)

    @app.post('/api/connections/{cid}/test')
    def test_connection(cid:str,request:Request):
        auth(request,admin=True)
        with store.transaction() as tx:record=need(tx,'connection',cid)
        try:
            db=connect(store,record)
            try:
                cursor=db.cursor();cursor.execute('SELECT 1 FROM DUAL' if record['dialect']=='oracle' else 'SELECT 1');cursor.fetchone();cursor.close();db.rollback()
            finally:db.close()
            success=True;message='Read-only connection test succeeded'
        except Exception:success=False;message='Connection test failed; check driver, endpoint, authentication and database permissions'
        with store.transaction() as tx:
            record=need(tx,'connection',cid);record.update(status='verified' if success else 'failed',last_test_at=stamp());tx.put('connection',record)
        return {'ok':success,'message':message}

    @app.get('/api/runtimes')
    def runtime_profiles(request:Request):
        auth(request);return runtimes()

    @app.get('/api/workflow-health')
    def health(request:Request):
        auth(request)
        with store.transaction() as tx:record=tx.get('meta','workflow_worker')
        return record or {'status':'unavailable','reason':'Workflow worker is not running'}

    def callback_auth(request,rid):
        run=service.get_run(rid)
        if not hmac.compare_digest(request.headers.get('x-workflow-token',''),run['callback_token']):raise HTTPException(403,detail={'code':'forbidden','message':'Invalid execution capability'})
        return run

    @app.post('/internal/workflows/{rid}/claim')
    def callback_claim(rid:str,request:Request,body:dict):
        callback_auth(request,rid);return service.claim_graph(rid,body.get('execution_id'))

    @app.post('/internal/workflows/{rid}/nodes/{nid}/submit',status_code=202)
    def callback_submit(rid:str,nid:str,request:Request,body:dict):
        callback_auth(request,rid);return service.submit_node(rid,nid,retry=body.get('retry') is True)

    @app.get('/internal/workflows/{rid}/nodes/{nid}/status')
    def callback_status(rid:str,nid:str,request:Request):
        callback_auth(request,rid);service._cancelled(rid);return service.node_status(rid,nid)

    @app.post('/internal/workflows/{rid}/nodes/{nid}')
    def callback_node(rid:str,nid:str,request:Request):
        callback_auth(request,rid)
        state=service.execute_node(rid,nid)
        deadline=time.monotonic()+service.get_run(rid)['timeout']+10
        while state['status']=='running' and time.monotonic()<deadline:
            time.sleep(.05);state=service.get_run(rid)['nodes'][nid]
        if state['status'] not in NODE_TERMINAL:raise WorkflowError('Node execution is still running','dependencies_pending',nid)
        return {'node_id':nid,'status':state['status']}

    @app.post('/internal/workflows/{rid}/finish')
    def callback_finish(rid:str,request:Request):
        callback_auth(request,rid);run=service.finish(rid)
        return {'run_id':rid,'status':run['status']}

    @app.post('/internal/workflow-tick')
    def callback_tick(request:Request):
        token=(store.path/'dispatch-token').read_text().strip()
        if not hmac.compare_digest(request.headers.get('x-dispatch-token',''),token):raise HTTPException(403,detail={'code':'forbidden','message':'Invalid dispatch token'})
        runs=service.tick();service.dispatch_pending();return {'admitted':len(runs)}
    return service


def main():
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('command',choices=['worker']);parser.parse_args()
    path=Path(os.environ.get('APP_STATE_DIR',os.environ.get('STATE_DIR','state'))).resolve()
    store=Store(path,os.environ.get('DATABASE_URL',f'sqlite:///{path}/console.db'));service=WorkflowService(store)
    from .workflows_n8n import worker_loop
    worker_loop(service)


if __name__=='__main__':main()
