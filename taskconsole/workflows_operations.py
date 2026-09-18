"""Durable workflow notifications and retention; business execution is independent."""
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import smtplib
import ssl
from datetime import datetime, timedelta
from email.message import EmailMessage
from urllib.parse import urlsplit
from urllib.request import Request as URLRequest, build_opener, HTTPRedirectHandler

from fastapi import Request, HTTPException
from .store import now, stamp, uid

ACTIVE = {'queued', 'running', 'cancelling'}
TERMINAL = {'succeeded', 'failed', 'partial', 'timed_out', 'cancelled'}
DEFAULT_POLICY = {'success_days': 30, 'failure_days': 30, 'metadata_days': 90}


class UncertainDelivery(ValueError):
    """A recipient may have accepted the message; blind retries can duplicate it."""


def validate_retention(data,defaults=None):
    if not isinstance(data,dict) or set(data)-set(DEFAULT_POLICY):raise ValueError('Invalid retention policy')
    policy={**(defaults or DEFAULT_POLICY),**data}
    if any(type(v) is not int or not 1<=v<=3650 for v in policy.values()) or policy['metadata_days']<max(policy['success_days'],policy['failure_days']):raise ValueError('Metadata retention must cover data retention; use 1–3650 days')
    return policy


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def public_channel(row):
    return {key:copy.deepcopy(row[key]) for key in ('id','name','kind','config','created_at','updated_at','target') if key in row} | {'has_secret':bool(row.get('secret')),'has_url':bool(row.get('encrypted_url'))}


def validate_channel(data):
    kind=data.get('kind');config=copy.deepcopy(data.get('config',{}))
    if kind not in {'webhook','email'} or not isinstance(config,dict):raise ValueError('Choose email or webhook')
    if not isinstance(data.get('name'),str) or not data['name'].strip():raise ValueError('Channel name is required')
    if kind=='webhook':
        url=urlsplit(config.get('url',''))
        if url.scheme not in {'https','http'} or not url.hostname or url.username or url.password or url.fragment:raise ValueError('Use an HTTP(S) webhook URL without embedded credentials')
        if url.scheme=='http' and url.hostname not in {'localhost','127.0.0.1','::1'}:raise ValueError('Remote webhooks require HTTPS')
        if config.get('provider','generic') not in {'generic','dingtalk','feishu','wecom'}:raise ValueError('Unknown webhook provider')
    else:
        for key in ('host','from'):
            if not isinstance(config.get(key),str) or not config[key] or any(c in config[key] for c in '\r\n'):raise ValueError('Invalid mail '+key)
        if not isinstance(config.get('to'),list) or not 1<=len(config['to'])<=100:raise ValueError('Choose 1–100 recipients')
        if any(not isinstance(v,str) or not re.fullmatch(r'[^\s@]+@[^\s@]+',v) for v in [config['from'],*config['to']]):raise ValueError('Invalid email address')
        if config.get('tls','starttls') not in {'starttls','ssl','none'}:raise ValueError('Invalid mail TLS mode')
        if config.get('tls')=='none' and config['host'] not in {'localhost','127.0.0.1','::1'}:raise ValueError('Remote SMTP requires TLS')
        if not 1<=int(config.get('port',587))<=65535:raise ValueError('Invalid mail port')
    # Store only documented fields; a token belongs in the encrypted secret field.
    allowed={'url','provider'} if kind=='webhook' else {'host','port','from','to','tls','username'}
    if set(config)-allowed:raise ValueError('Unsupported channel setting')
    return kind,config


class WorkflowOperations:
    def __init__(self,store):self.store=store

    def channels(self):
        with self.store.transaction() as tx:return [public_channel(x) for x in tx.all('workflow_channel')]

    def save_channel(self,data,cid=None):
        data=copy.deepcopy(data)
        with self.store.transaction() as tx:existing=tx.get('workflow_channel',cid) if cid else None
        if data.get('kind')=='webhook' and not data.get('config',{}).get('url') and existing and existing.get('encrypted_url'):
            data.setdefault('config',{})['url']=self.store.fernet.decrypt(existing['encrypted_url'].encode()).decode()
        kind,config=validate_channel(data)
        with self.store.transaction() as tx:
            old=tx.get('workflow_channel',cid) if cid else None
            row={'id':cid or uid(),'name':data['name'].strip()[:120],'kind':kind,'config':config,'created_at':(old or {}).get('created_at',stamp()),'updated_at':stamp(),'secret':(old or {}).get('secret')}
            if kind=='webhook':
                url=config['url'];parsed=urlsplit(url)
                row['encrypted_url']=self.store.fernet.encrypt(url.encode()).decode()
                row['target']=parsed.scheme+'://'+parsed.netloc+'/…'
                row['config']['url']=''
            else:row['target']=', '.join(config['to'])
            if 'secret' in data:
                if not isinstance(data['secret'],str) or len(data['secret'])>16384:raise ValueError('Invalid channel secret')
                row['secret']=self.store.fernet.encrypt(data['secret'].encode()).decode() if data['secret'] else None
            tx.put('workflow_channel',row)
        return public_channel(row)

    def _mask(self,text):
        text=str(text or '')
        with self.store.transaction() as tx:
            secrets=[r.get(k) for r in tx.all('workflow_channel') for k in ('secret','encrypted_url')]
            secrets += [r.get('encrypted') for r in tx.all('variable')+tx.all('workflow_credential')]
            secrets += [r.get('encrypted_config') for r in tx.all('connection')]
        for value in secrets:
            if not value:continue
            try:plain=self.store.fernet.decrypt(value.encode()).decode()
            except Exception:continue
            if plain:text=text.replace(plain,'[redacted]')
            try:
                parsed=json.loads(plain)
                if isinstance(parsed,dict):
                    for v in parsed.values():
                        if isinstance(v,str) and v:text=text.replace(v,'[redacted]')
            except (ValueError,TypeError):pass
        return text[:1000]

    def on_run_terminal(self,run_id):
        with self.store.transaction() as tx:
            run=tx.get('workflow_run',run_id)
            if not run or run['status'] not in TERMINAL:return
            if tx.get('workflow_notice_seen',run_id):return
            policy=run.get('snapshot',{}).get('notifications',{})
            previous=tx.get('workflow_notice_state',run['workflow_id'])
            event=run['status']
            if event=='succeeded' and previous and previous.get('status') in {'failed','partial','timed_out'}:event='recovery'
            if not run.get('test'):
                tx.put('workflow_notice_state',{'id':run['workflow_id'],'status':run['status'],'run_id':run_id})
            tx.put('workflow_notice_seen',{'id':run_id,'at':stamp()})
            if run.get('test') and not policy.get('include_tests',False):return
            if event not in policy.get('events',[]):return
            for cid in policy.get('channel_ids',[]):
                if not tx.get('workflow_channel',cid):continue
                key=hashlib.sha256((run_id+':'+cid+':'+event).encode()).hexdigest()
                if not tx.get('workflow_notification',key):tx.put('workflow_notification',{'id':key,'run_id':run_id,'workflow_id':run['workflow_id'],'channel_id':cid,'event':event,'status':'pending','attempts':0,'created_at':stamp()})

    def _payload(self,run,event):
        base=os.environ.get('SLEEP_IN_PUBLIC_URL','http://localhost:8765').rstrip('/')
        return {'event':event,'workflow_id':run['workflow_id'],'workflow':run.get('workflow_name',''),'run_id':run['id'],'time':run.get('finished_at') or stamp(),'error':self._mask(run.get('error')),'url':base+'/workflow-runs/'+run['id']}

    def _send(self,channel,payload):
        config=copy.deepcopy(channel['config']);secret=self.store.fernet.decrypt(channel['secret'].encode()).decode() if channel.get('secret') else ''
        if channel.get('encrypted_url'):config['url']=self.store.fernet.decrypt(channel['encrypted_url'].encode()).decode()
        text=f"{payload['workflow']}: {payload['event']}\n{payload['time']}\n{payload['error']}\n{payload['url']}"
        if channel['kind']=='webhook':
            provider=config.get('provider','generic');body=payload
            if provider in {'dingtalk','wecom'}:body={'msgtype':'text','text':{'content':text}}
            if provider=='feishu':body={'msg_type':'text','content':{'text':text}}
            headers={'Content-Type':'application/json'}
            if secret:headers['Authorization']='Bearer '+secret
            request=URLRequest(config['url'],data=json.dumps(body).encode(),headers=headers,method='POST')
            with build_opener(NoRedirect).open(request,timeout=10) as response:
                result=response.read(65537)
                if len(result)>65536:raise ValueError('Webhook response too large')
                if provider!='generic' and result:
                    value=json.loads(result)
                    if value.get('errcode',value.get('code',0))!=0:raise ValueError('Provider rejected the notification')
        else:
            message=EmailMessage();message['Subject']=f"Sleep In · {payload['event']} · {payload['workflow']}";message['From']=config['from'];message['To']=', '.join(config['to']);message.set_content(text)
            mode=config.get('tls','starttls');factory=smtplib.SMTP_SSL if mode=='ssl' else smtplib.SMTP
            kwargs={'timeout':10}
            if mode=='ssl':kwargs['context']=ssl.create_default_context()
            accepted=False;delivery_error=None
            try:
                with factory(config['host'],int(config.get('port',465 if mode=='ssl' else 587)),**kwargs) as smtp:
                    if mode=='starttls':smtp.starttls(context=ssl.create_default_context())
                    if config.get('username'):smtp.login(config['username'],secret)
                    try:
                        refused=smtp.send_message(message)
                    except (smtplib.SMTPSenderRefused,smtplib.SMTPRecipientsRefused,smtplib.SMTPDataError) as exc:
                        delivery_error=exc;raise  # Explicit rejection before acceptance.
                    except (smtplib.SMTPServerDisconnected,OSError) as exc:
                        delivery_error=UncertainDelivery('Email acknowledgement was lost; inspect recipients before resending')
                        raise delivery_error from exc
                    accepted=True
                    if refused:
                        delivery_error=UncertainDelivery('Partial email delivery: some recipients accepted the message; inspect recipients before resending')
                        raise delivery_error
            except Exception as exc:
                # QUIT/context teardown must not replace the actual DATA outcome.
                if delivery_error is not None:
                    if exc is delivery_error:raise
                    raise delivery_error from exc
                if accepted:raise UncertainDelivery('Email was accepted but session closure failed; inspect recipients before resending') from exc
                raise

    def test_channel(self,cid):
        with self.store.transaction() as tx:channel=tx.get('workflow_channel',cid)
        if not channel:raise ValueError('Channel not found')
        self._send(channel,{'event':'test','workflow_id':'test','workflow':'Connection test','run_id':'test','time':stamp(),'error':'','url':os.environ.get('SLEEP_IN_PUBLIC_URL','http://localhost:8765')})
        return {'status':'sent','target':channel['target']}

    def deliveries(self):
        with self.store.transaction() as tx:return sorted(tx.all('workflow_notification'),key=lambda r:r['created_at'],reverse=True)

    def retry_delivery(self,delivery_id,expected_attempt):
        """Retry only an explicitly selected failed delivery, never its business run."""
        if type(expected_attempt) is not int or expected_attempt<1:raise ValueError('Choose the failed delivery attempt to retry')
        with self.store.transaction() as tx:
            row=tx.get('workflow_notification',delivery_id)
            if not row:raise ValueError('Notification delivery not found')
            if row['status']=='uncertain':raise ValueError('Delivery outcome is uncertain; inspect the recipient before resending')
            if expected_attempt>row['attempts']:raise ValueError('Notification attempt changed; refresh delivery history')
            if expected_attempt<row['attempts'] or row['status'] in {'pending','sending','sent'}:return copy.deepcopy(row)
            if row['status']!='failed':raise ValueError('Only a failed notification can be retried')
            row.update(status='pending',retry_requested_at=stamp())
            tx.put('workflow_notification',row)
        return copy.deepcopy(row)

    def tick(self):
        with self.store.transaction() as tx:runs=tx.all('workflow_run')
        for run in sorted(runs,key=lambda r:r.get('finished_at') or r['created_at']):self.on_run_terminal(run['id'])
        for candidate in self.deliveries():
            with self.store.transaction() as tx:
                row=tx.get('workflow_notification',candidate['id'])
                if row['status']=='sending' and datetime.fromisoformat(row['started_at'])<now()-timedelta(minutes=2):
                    row.update(status='uncertain',error='Delivery interrupted; inspect recipient before resending',finished_at=stamp());tx.put('workflow_notification',row)
                if row['status']!='pending':continue
                row.update(status='sending',attempts=row['attempts']+1,started_at=stamp());tx.put('workflow_notification',row)
                run=tx.get('workflow_run',row['run_id']);channel=tx.get('workflow_channel',row['channel_id'])
            try:
                if not run or not channel:raise ValueError('Run or channel unavailable')
                self._send(channel,self._payload(run,row['event']));result={'status':'sent','error':None}
            except Exception as exc:result={'status':'uncertain' if isinstance(exc,UncertainDelivery) else 'failed','error':self._mask(type(exc).__name__+': '+str(exc))}
            with self.store.transaction() as tx:
                row=tx.get('workflow_notification',row['id']);row.update(**result,finished_at=stamp());tx.put('workflow_notification',row)
        with self.store.transaction() as tx:last=tx.get('meta','workflow_cleanup')
        if not last or datetime.fromisoformat(last['at'])<now()-timedelta(hours=1):self.cleanup()

    def policy(self):
        with self.store.transaction() as tx:return (tx.get('meta','workflow_retention') or {'policy':DEFAULT_POLICY})['policy']

    def save_policy(self,data):
        policy=validate_retention(data)
        with self.store.transaction() as tx:tx.put('meta',{'id':'workflow_retention','policy':policy})
        return policy

    def _retention_plan(self,tx):
        policy=(tx.get('meta','workflow_retention') or {'policy':DEFAULT_POLICY})['policy']
        runs=tx.all('workflow_run');protected=set()
        for run in runs:
            if run['status'] in ACTIVE:
                protected.add(run['id']);protected.update(run.get('input_run_ids',[]))
                def refs(value):
                    if isinstance(value,dict):
                        if value.get('run_id'):protected.add(value['run_id'])
                        for v in value.values():refs(v)
                    elif isinstance(value,list):
                        for v in value:refs(v)
                refs(run.get('snapshot',{}));refs(run.get('nodes',{}))
        data=[];metadata=[]
        for run in runs:
            if run['id'] in protected or not run.get('finished_at'):continue
            age=(now()-datetime.fromisoformat(run['finished_at'])).total_seconds()/86400
            try:selected=validate_retention(run.get('snapshot',{}).get('retention',{}),policy)
            except ValueError:continue  # Corrupt historical overrides must not destroy records.
            days=selected['success_days'] if run['status']=='succeeded' else selected['failure_days']
            if age>days and not run.get('data_expired'):data.append(run['id'])
            if age>selected['metadata_days'] and (run.get('data_expired') or run['id'] in data):metadata.append(run['id'])
        return {'data_run_ids':sorted(data),'metadata_run_ids':sorted(metadata),'protected_run_ids':sorted(protected)}

    def retention_preview(self):
        with self.store.transaction() as tx:return self._retention_plan(tx)

    def cleanup(self):
        # Stage deletions on the same filesystem. The database must commit first.
        # A leftover staged directory is reconciled on the next cleanup after a crash.
        trash=self.store.path/'.workflow-retention-trash'
        if trash.is_symlink() or (self.store.path/'workflow-runs').is_symlink():raise ValueError('Refusing retention cleanup through symlink managed roots')
        trash.mkdir(exist_ok=True,mode=0o700)
        with self.store.lock:
            with self.store.transaction() as tx:
                for staged in trash.iterdir():
                    if staged.is_symlink() or not staged.is_dir() or not re.fullmatch(r'[A-Za-z0-9_-]+',staged.name):raise ValueError('Unsafe retention staging directory')
                    record=tx.get('workflow_run',staged.name)
                    destination=self.store.path/'workflow-runs'/staged.name
                    if record and not record.get('data_expired'):
                        if destination.exists():raise ValueError('Retention recovery destination already exists')
                        destination.parent.mkdir(exist_ok=True);staged.rename(destination)
                    else:shutil.rmtree(staged)
            moved=[]
            try:
                with self.store.transaction() as tx:
                    plan=self._retention_plan(tx)
                    for rid in plan['data_run_ids']:
                        directory=self.store.path/'workflow-runs'/rid
                        if not re.fullmatch(r'[A-Za-z0-9_-]+',rid):raise ValueError('Invalid stored run identifier')
                        if directory.is_symlink():raise ValueError('Refusing retention cleanup through symlink')
                        if directory.exists():
                            staged=trash/rid;directory.rename(staged);moved.append((staged,directory))
                        run=tx.get('workflow_run',rid);run['artifacts']=[];run['data_expired']=True
                        for node in run.get('nodes',{}).values():
                            for key in ('inputs','output','stdout','stderr'):node.pop(key,None)
                            for attempt in node.get('attempts',[]):
                                for key in ('stdout','stderr','output','inputs'):attempt.pop(key,None)
                        tx.put('workflow_run',run)
                    for rid in plan['metadata_run_ids']:tx.remove('workflow_run',rid)
                    tx.put('meta',{'id':'workflow_cleanup','at':stamp(),'result':plan})
            except BaseException:
                for staged,destination in reversed(moved):staged.rename(destination)
                raise
            for staged,_ in moved:shutil.rmtree(staged)
        return plan


def register_operations_routes(app,store,require):
    op=WorkflowOperations(store)
    def auth(request):
        with store.transaction() as tx:return require(request,tx,True)
    @app.get('/api/workflow-channels')
    def channels(request:Request):auth(request);return op.channels()
    @app.post('/api/workflow-channels')
    def create(request:Request,data:dict):auth(request);return op.save_channel(data)
    @app.put('/api/workflow-channels/{cid}')
    def update(cid:str,request:Request,data:dict):auth(request);return op.save_channel(data,cid)
    @app.post('/api/workflow-channels/{cid}/test')
    def test(cid:str,request:Request):
        auth(request)
        try:return op.test_channel(cid)
        except Exception as exc:raise HTTPException(422,{'message':op._mask(str(exc))}) from exc
    @app.get('/api/workflow-notifications')
    def notifications(request:Request):auth(request);return op.deliveries()
    @app.post('/api/workflow-notifications/{delivery_id}/retry')
    def retry_notification(delivery_id:str,request:Request,data:dict):
        auth(request);return op.retry_delivery(delivery_id,data.get('expected_attempt'))
    @app.get('/api/workflow-maintenance')
    def maintenance(request:Request):
        auth(request)
        with store.transaction() as tx:
            last=tx.get('meta','workflow_cleanup');paused=(tx.get('meta','workflow_maintenance') or {}).get('paused',False)
        return {'policy':op.policy(),'preview':op.retention_preview(),'last_cleanup':last,'paused':paused}
    @app.post('/api/workflow-maintenance/policy')
    def policy(request:Request,data:dict):auth(request);return op.save_policy(data)
    @app.post('/api/workflow-maintenance/cleanup')
    def cleanup(request:Request):auth(request);return op.cleanup()
    @app.post('/api/workflow-maintenance/pause')
    def pause(request:Request,data:dict):
        auth(request)
        if type(data.get('paused')) is not bool:raise ValueError('paused must be a boolean')
        with store.transaction() as tx:tx.put('meta',{'id':'workflow_maintenance','paused':data['paused']})
        return {'paused':data['paused']}
    return op
