"""Execution inputs, isolated node tests and short-lived adapter submissions."""
import copy
import hashlib
import json
import threading
from pathlib import Path
from datetime import datetime
from .store import uid,stamp,now

MAX_DATA=100*1024*1024


def retry_policy(node):
    policy=node.get('config',{}).get('retry',{})
    if not isinstance(policy,dict):raise ValueError('Retry policy must be an object')
    attempts=policy.get('max_attempts',1);delay=policy.get('delay_seconds',0)
    if type(attempts) is not int or not 1<=attempts<=5:raise ValueError('Retry max_attempts must be 1..5')
    if type(delay) not in (int,float) or not 0<=delay<=60:raise ValueError('Retry delay_seconds must be 0..60')
    if attempts>1 and policy.get('safe_to_retry') is not True:raise ValueError('Retries require explicit safe_to_retry')
    if attempts>1 and node['kind']=='sql' and node.get('config',{}).get('mode')=='write' and policy.get('idempotent') is not True:raise ValueError('SQL write retries require explicit idempotent declaration')
    return attempts,delay


def verified_file(store,rid,item):
    path=Path(item['path']);root=(store.path/'workflow-runs'/rid).resolve()
    if path.is_symlink() or not path.resolve().is_relative_to(root) or not path.is_file():raise ValueError('Artifact missing or outside authorized run')
    if path.stat().st_size>MAX_DATA or path.stat().st_size!=item['size']:raise ValueError('Artifact size or quota mismatch')
    data=path.read_bytes()
    if hashlib.sha256(data).hexdigest()!=item['sha256']:raise ValueError('Artifact checksum changed')
    return data


def output_data(store,run,state):
    output=state['output']
    if output.get('data_ref'):return json.loads(verified_file(store,run['id'],output['data_ref']))
    return output['data']


def materialize_artifact(store,run,binding,directory):
    items=[a for a in run['artifacts'] if a['node_id']==binding['node_id'] and a['name']==binding.get('name')]
    if len(items)!=1:raise KeyError('Artifact unavailable or ambiguous')
    item=items[0];data=verified_file(store,run['id'],item)
    target=directory/'inputs'/item['id']/Path(item['name']).name
    if target.exists():verified_file(store,run['id'],{**item,'path':str(target)})
    else:
        existing=[p for p in (directory/'inputs').rglob('*') if p.is_file()]
        if len(existing)>=100 or sum(p.stat().st_size for p in existing)+len(data)>MAX_DATA:raise ValueError('Artifact input quota exceeded')
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(data);target.chmod(0o400)
    return {k:v for k,v in {**item,'path':str(target)}.items() if k in {'path','name','mediaType','size','sha256'}}


def spill_output(result,directory):
    output=result.get('output')
    if not output:return
    raw=json.dumps(output['data'],ensure_ascii=False,allow_nan=False).encode()
    if len(raw)<=1024*1024:return
    if len(raw)+sum(a['size'] for a in output['artifacts'])>MAX_DATA or len(output['artifacts'])>=100:raise ValueError('Artifact quota exceeded')
    directory.mkdir(parents=True,exist_ok=True);path=directory/'complete-data.json';path.write_bytes(raw)
    item={'name':'complete-data.json','path':str(path),'mediaType':'application/json','size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'role':'complete-data'}
    output['artifacts'].append(item);output['data_ref']=item;output['data']={}


class ExecutionMixin:
    def test_node(self,wid,nid,body):
        from .workflows import need,WorkflowError
        from .workflows_runtime import check_schema,portable
        if ('inputs' in body)==('sample_run_id' in body):raise WorkflowError('Provide explicit inputs or one authorized historical sample')
        with self.store.transaction() as tx:
            wf=need(tx,'workflow',wid)
            if 'sample_run_id' in body:
                sample=need(tx,'workflow_run',body['sample_run_id'])
                if sample['workflow_id']!=wid:raise WorkflowError('Historical sample belongs to another workflow')
                snid=body.get('sample_node_id',nid)
                if snid not in sample['nodes'] or 'inputs' not in sample['nodes'][snid]:raise WorkflowError('Historical sample has no recorded inputs')
                inputs=copy.deepcopy(sample['nodes'][snid]['inputs'])
                provenance={'kind':'historical_sample','run_id':sample['id'],'node_id':snid,'version_id':sample['version_id'],'produced_at':sample['nodes'][snid].get('finished_at')}
                # Artifact paths and secret references must be rebound, never silently replayed.
                old=next(n for n in sample['snapshot']['nodes'] if n['id']==snid)
                if any(b.get('source') in {'artifact','credential'} for b in old.get('inputs',{}).values()):raise WorkflowError('Rebind artifact or credential inputs explicitly')
            else:
                inputs=copy.deepcopy(body['inputs']);provenance={'kind':'explicit'}
        if not isinstance(inputs,dict):raise WorkflowError('Test inputs must be an object')
        node=next((n for n in wf['nodes'] if n['id']==nid),None)
        if not node:raise WorkflowError('Node not found','not_found')
        node=copy.deepcopy(node);check_schema(inputs,node.get('input_schema',{}),'inputs');portable(inputs)
        node['inputs']={k:{'source':'constant','value':v} for k,v in inputs.items()}
        prepared=copy.deepcopy(wf);prepared.update(nodes=[node],edges=[],test_node_id=nid,test_input_source=provenance)
        return self.admit(wid,{},test=True,prepared_test=prepared)

    def node_status(self,rid,nid):
        from .workflows import NODE_TERMINAL,WorkflowError
        run=self.get_run(rid)
        if nid not in run['nodes']:raise WorkflowError('Node not found','not_found')
        state=run['nodes'][nid];node=next(n for n in run['snapshot']['nodes'] if n['id']==nid)
        limit,delay=retry_policy(node)
        eligible=(state['status']=='failed' and state.get('process_started') and state.get('retryable') is not False and len(state['attempts'])<limit and run['status'] in {'queued','running'} and state.get('reason')!='interrupted_unknown_effect')
        ready=eligible and (now()-datetime.fromisoformat(state['finished_at'])).total_seconds()>=delay
        return {'node_id':nid,'status':state['status'],'terminal':state['status'] in NODE_TERMINAL and not eligible,'retry_ready':bool(ready),'attempt':len(state['attempts'])}

    def submit_node(self,rid,nid,retry=False):
        from .workflows import need,WorkflowError
        # Store transaction is shared between request threads; dispatching is the durable claim.
        with self.store.lock:
            status=self.node_status(rid,nid)
            with self.store.transaction() as tx:
                run=need(tx,'workflow_run',rid);state=run['nodes'][nid]
                if state['status'] not in {'queued','failed'} or run['status'] not in {'queued','running'}:return status
                if state['status']=='failed' and not (retry and status['retry_ready']):return status
                state['status']='dispatching';tx.put('workflow_run',run)
            thread=threading.Thread(target=self.execute_node,args=(rid,nid),daemon=True,name='node-'+rid+'-'+nid)
            self._threads[rid+':'+nid]=thread;thread.start()
        return self.node_status(rid,nid)

    def claim_graph(self,rid,execution_id):
        from .workflows import need,WorkflowError
        if not isinstance(execution_id,str) or not execution_id:raise WorkflowError('Execution identity required')
        with self.store.transaction() as tx:
            run=need(tx,'workflow_run',rid)
            if run.get('graph_execution_id') not in {None,execution_id}:raise WorkflowError('Another n8n execution owns this run','duplicate_execution')
            run['graph_execution_id']=execution_id;tx.put('workflow_run',run)
        return {'run_id':rid,'claimed':True}

    def trigger_run(self,wid,tid,body):
        from .workflows import need,WorkflowError
        from .workflows_runtime import check_schema
        with self.store.transaction() as tx:
            wf=need(tx,'workflow',wid)
            trigger=next((t for t in wf.get('triggers',[]) if t['id']==tid),None)
            if not trigger:raise WorkflowError('Trigger not found','not_found')
            if not trigger.get('enabled'):raise WorkflowError('Trigger is disabled')
            if trigger['kind'] not in {'manual','api'}:raise WorkflowError('Scheduled trigger is admitted by its schedule')
        params=copy.deepcopy(trigger.get('params',{}));params.update(body.get('params',{}))
        check_schema(params,trigger.get('parameter_schema',{}),'params')
        return self.admit(wid,params,key=body.get('idempotency_key'),trigger_id=tid,version_id=trigger.get('version_id'),overlap=trigger.get('overlap','skip'),queue_limit=trigger.get('queue_limit',10))

    def save_credential(self,body,cid=None):
        from .workflows import need,WorkflowError
        with self.store.transaction() as tx:
            record=need(tx,'workflow_credential',cid) if cid else {'id':uid(),'created_at':stamp(),'revision':uid(),'name':'Credential','allowed_workflows':[]}
            if 'name' in body:record['name']=str(body['name'])[:200]
            if 'allowed_workflows' in body:
                allowed=body['allowed_workflows']
                if not isinstance(allowed,list) or any(not isinstance(v,str) for v in allowed):raise WorkflowError('Allowed workflows must be an ID array')
                record['allowed_workflows']=allowed
            if 'value' in body:
                if not isinstance(body['value'],str) or not body['value'] or len(body['value'])>65536:raise WorkflowError('Credential must be nonempty text up to 64 KiB')
                record['encrypted']=self.store.fernet.encrypt(body['value'].encode()).decode();record['revision']=uid()
            if not record.get('encrypted'):raise WorkflowError('Credential value required')
            tx.put('workflow_credential',record)
        return {k:v for k,v in record.items() if k!='encrypted'}


def mask_secrets(value,secrets):
    if isinstance(value,str):
        for secret in sorted(set(secrets),key=len,reverse=True):
            if secret:value=value.replace(secret,'[redacted]')
        return value
    if isinstance(value,dict):return {k:mask_secrets(v,secrets) for k,v in value.items()}
    if isinstance(value,list):return [mask_secrets(v,secrets) for v in value]
    return value


def run_name(snapshot,params):
    import re
    template=snapshot.get('run_name_template','{workflow} · {date}')
    if not isinstance(template,str) or len(template)>500:raise ValueError('Run name template must be text up to 500 characters')
    def replace(match):
        key=match.group(1)
        if key=='workflow':return snapshot['name']
        if key=='date':return now().date().isoformat()
        if key.startswith('params.'):
            from .workflows_runtime import resolve_path
            value=resolve_path(params,key[7:])
            if isinstance(value,(dict,list)):raise ValueError('Run name parameters must be scalar')
            return str(value)
        raise ValueError('Unknown run name placeholder '+key)
    return re.sub(r'\{([^{}]+)\}',replace,template)[:500]


def validate_operating_policies(store,wf):
    from .workflows import WorkflowError
    from .workflows_operations import validate_retention
    if 'retention' in wf:
        try:wf['retention']=validate_retention(wf['retention'])
        except ValueError as exc:raise WorkflowError(str(exc))
    policy=wf.get('notifications',{})
    if not isinstance(policy,dict):raise WorkflowError('Notification policy must be an object')
    channels=policy.get('channel_ids',[]);events=policy.get('events',[])
    if not isinstance(channels,list) or any(not isinstance(c,str) for c in channels):raise WorkflowError('Notification channel IDs must be an array')
    if not isinstance(events,list) or any(e not in {'failed','partial','timed_out','cancelled','recovery','succeeded'} for e in events):raise WorkflowError('Unknown notification event')
    if type(policy.get('include_tests',False)) is not bool:raise WorkflowError('Notification include_tests must be boolean')
    with store.transaction() as tx:
        if any(not tx.get('workflow_channel',cid) for cid in channels):raise WorkflowError('Notification channel does not exist')


def process_identity(pid):
    """OS start identity and working directory; PID alone is never a kill authority."""
    import os,subprocess
    try:
        process=subprocess.run(['/bin/ps','-p',str(pid),'-o','lstart=','-o','pgid='],capture_output=True,text=True,timeout=2)
        identity=process.stdout.strip()
        if process.returncode or not identity or int(identity.split()[-1])!=pid:return None
        proc=Path('/proc')/str(pid)/'cwd'
        if proc.exists():cwd=os.readlink(proc)
        else:
            result=subprocess.run(['/usr/sbin/lsof','-a','-p',str(pid),'-d','cwd','-Fn'],capture_output=True,text=True,timeout=2)
            cwd=next((line[1:] for line in result.stdout.splitlines() if line.startswith('n')),None)
        if not cwd:return None
        return {'start_and_group':identity,'cwd':str(Path(cwd).resolve())}
    except (OSError,ValueError,subprocess.SubprocessError):return None


def terminate_orphan(pid,identity,run_root):
    import os,signal,time
    if not pid:return 'no_worker'
    if not identity or process_identity(pid)!=identity:return 'identity_unverified'
    if not Path(identity['cwd']).is_relative_to(Path(run_root).resolve()):return 'identity_unverified'
    try:
        os.killpg(pid,signal.SIGTERM)
        deadline=time.monotonic()+1
        while time.monotonic()<deadline and process_identity(pid)==identity:time.sleep(.05)
        # Never signal a group again after its leader identity is gone/reused.
        if process_identity(pid)==identity:
            try:os.killpg(pid,signal.SIGKILL)
            except ProcessLookupError:pass
        return 'termination_sent'
    except ProcessLookupError:return 'already_exited'
    except PermissionError:return 'termination_unconfirmed'


def sanitize_result(result,secrets):
    """Mask human-visible data without corrupting protocol paths/checksums."""
    if not secrets:return result
    for key in ('stdout','stderr','error'):
        if key in result:result[key]=mask_secrets(result[key],secrets)
    if result.get('output'):
        result['output']['data']=mask_secrets(result['output']['data'],secrets)
        for item in result['output']['artifacts']:
            path=Path(item['path']);raw=path.read_bytes()
            if any(secret.encode() in raw for secret in secrets if secret):
                try:clean=mask_secrets(raw.decode('utf-8'),secrets).encode('utf-8')
                except UnicodeDecodeError:raise ValueError('Artifact contains a known credential; remove it before export')
                path.write_bytes(clean);item.update(size=len(clean),sha256=hashlib.sha256(clean).hexdigest(),redacted=True)
            item['name']=mask_secrets(item['name'],secrets)
    return result
