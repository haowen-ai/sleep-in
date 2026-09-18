"""Same-origin web API. All user operations stay in one trusted workspace."""
import hashlib
import hmac
import json
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit
from fastapi import FastAPI, Request, HTTPException, UploadFile, File, Form
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from . import __version__
from .store import Store, now, stamp, uid
from .auth import password_hash, password_valid, public_user, revoke, session_create, session_lookup
from .schedule import next_runs
from .service import audit, validate_manifest, valid_variable_name, task_data, enqueue, tick, ACTIVE


def fail(status,code,message):
    raise HTTPException(status,detail={'code':code,'message':message})


def initialize(store):
    samples=[('Hello, schedule','Print a friendly greeting.','print("Hello from Sleep In!")\n',{}),('Parameters in action','Pass a name to your Python function.','def main(params):\n    print("Hello, " + params.get("name", "world") + "!")\n',{'parameters':[{'key':'name','help':'Who should we greet?','default':'world','required':False}]}),('Create an output file','Generate a downloadable text file.','import os\nfrom pathlib import Path\nPath(os.environ["TASK_OUTPUT_DIR"], "hello.txt").write_text("Hello from your scheduled Python task!\\n", encoding="utf-8")\nprint("Created hello.txt")\n',{})]
    with store.transaction() as tx:
        for i,(name,description,source,manifest) in enumerate(samples):
            sid=f'sample-{i+1}';vid=f'{sid}-v1'
            if tx.get('script',sid):continue
            folder=store.path/'scripts'/vid;folder.mkdir(exist_ok=True)
            (folder/'main.py').write_text(source,encoding='utf-8')
            script={'id':sid,'name':name,'description':description,'source':source,'manifest':manifest,'requirements':'','archived':False,'default_version':vid,'created_at':stamp(),'draft_dir':None,'sample':True}
            tx.put('script',script)
            tx.put('version',{'id':vid,'script_id':sid,'number':1,'status':'published','mode':'function' if i==1 else 'script','manifest':manifest,'created_at':stamp(),'digest':hashlib.sha256(source.encode()).hexdigest(),'runtime':{'python':sys.executable,'freeze':'','log':'Standard library environment'},'build_log':'Standard library environment','freeze':''})


def create_app(state_dir=None,database_url=None):
    directory=Path(state_dir or os.environ.get('APP_STATE_DIR','state')).resolve()
    store=Store(directory,database_url or os.environ.get('DATABASE_URL',f'sqlite:///{directory}/console.db'))
    initialize(store)
    app=FastAPI(title='Sleep In',version=__version__,docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store

    @app.middleware('http')
    async def boundaries(request,call_next):
        length=request.headers.get('content-length','0')
        if length.isdigit() and int(length)>26*1048576:
            return JSONResponse({'detail':{'code':'too_large','message':'Request exceeds 26 MiB'}},status_code=413)
        if request.method not in {'GET','HEAD','OPTIONS'} and request.headers.get('origin'):
            if urlsplit(request.headers['origin']).netloc!=request.headers.get('host'):
                return JSONResponse({'detail':{'code':'csrf','message':'Cross-origin request rejected'}},status_code=403)
        response=await call_next(request)
        response.headers['X-Content-Type-Options']='nosniff'
        response.headers['X-Frame-Options']='DENY'
        response.headers['Referrer-Policy']='same-origin'
        response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        response.headers['Cache-Control']='no-store'
        return response

    @app.exception_handler(ValueError)
    async def value_error(request,exc):
        return JSONResponse({'detail':{'code':'invalid','message':str(exc)}},status_code=400)

    @app.exception_handler(RequestValidationError)
    async def request_validation_error(request,exc):
        errors=exc.errors()
        message=errors[0].get('msg','Invalid request') if errors else 'Invalid request'
        return JSONResponse({'detail':{'code':'validation','message':message}},status_code=422)

    def require(request,tx,admin=False):
        user,session=session_lookup(tx,request.cookies.get('console_session'))
        if not user:fail(401,'unauthorized','Please sign in')
        if request.method not in {'GET','HEAD'} and not hmac.compare_digest(request.headers.get('x-csrf-token',''),session['csrf']):
            fail(403,'csrf','Refresh the page and try again')
        if admin and user['role']!='admin':fail(403,'forbidden','Administrator access required')
        return user

    def get(tx,kind,key):
        obj=tx.get(kind,key)
        if not obj:fail(404,'not_found','Item not found')
        return obj

    def login_response(tx,user):
        raw,session=session_create(tx,user)
        response=JSONResponse({'user':public_user(user),'csrf':session['csrf']})
        response.set_cookie('console_session',raw,httponly=True,samesite='strict',secure=os.environ.get('COOKIE_SECURE','false').lower()=='true',max_age=43200)
        return response

    def scheduler(settings):
        last=settings.get('last_tick')
        healthy=last and (now()-datetime.fromisoformat(last)).total_seconds()<30
        return {'status':'ready' if healthy else 'unavailable','last_tick':last}

    def show_execution(run):
        fields=('id','task_id','task_name','status','trigger','created_at','started_at','finished_at','reason','exit_code','params','version_id','script_name','timezone','artifacts','logs_expired','logs_truncated')
        return {key:run.get(key) for key in fields}

    def show_task(task,settings):
        return {**task,'sync_status': 'disabled' if not task['enabled'] else scheduler(settings)['status']}

    def show_script(tx,script,admin=False,detail=False):
        result={k:v for k,v in script.items() if k not in {'source','draft_dir','requirements'}}
        versions=[v for v in tx.all('version') if v['script_id']==script['id'] and (admin or v['status']=='published')]
        result['versions']=sorted(versions,key=lambda v:v['number'],reverse=True)
        if not admin:
            for version in result['versions']:
                version.pop('runtime',None);version.pop('build_log',None);version.pop('freeze',None)
        if admin and detail:
            result.update(source=script.get('source',''),requirements=script.get('requirements',''))
        return result

    @app.get('/healthz')
    def health():return {'status':'ok'}

    @app.get('/readyz')
    def ready():
        with store.transaction() as tx:tx.get('meta','settings')
        return {'status':'ok'}

    @app.get('/api/bootstrap')
    def bootstrap(request:Request):
        with store.transaction() as tx:
            user,session=session_lookup(tx,request.cookies.get('console_session'))
            settings=tx.get('meta','settings')
            return {'initialized':bool(tx.all('user')),'user':public_user(user) if user else None,'csrf':session['csrf'] if session else None,'timezone':settings['timezone'],'scheduler':scheduler(settings),'version':__version__}

    @app.post('/api/setup')
    def setup(request:Request,data:dict):
        with store.transaction() as tx:
            if tx.all('user'):fail(409,'already_initialized','Setup is already complete')
            if not hmac.compare_digest(str(data.get('token','')),(store.path/'setup-token').read_text().strip()):
                fail(403,'invalid_token','Invalid setup token')
            from zoneinfo import ZoneInfo
            try:ZoneInfo(data.get('timezone','UTC'))
            except Exception:raise ValueError('Invalid IANA timezone')
            user=new_user(tx,data,'admin')
            settings=tx.get('meta','settings');settings['timezone']=data.get('timezone','UTC');tx.put('meta',settings)
            audit(tx,user,'setup',user['id'])
            return login_response(tx,user)

    def new_user(tx,data,forced_role=None):
        username=data.get('username','')
        if not isinstance(username,str):raise ValueError('Username must be text')
        username=username.strip()
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',username):raise ValueError('Username must contain 1–64 letters, digits, dots, underscores or hyphens')
        if any(u['username'].lower()==username.lower() for u in tx.all('user')):fail(409,'conflict','Username already exists')
        role=forced_role or data.get('role','operator')
        locale=data.get('locale','en')
        if role not in {'admin','operator'} or locale not in {'en','zh-CN'}:raise ValueError('Invalid role or locale')
        user={'id':uid(),'username':username,'password':password_hash(data.get('password')),'role':role,'locale':locale,'enabled':True,'created_at':stamp()}
        tx.put('user',user);return user

    @app.post('/api/login')
    def login(request:Request,data:dict):
        ip=request.client.host if request.client else 'unknown'
        key=hashlib.sha256(ip.encode()).hexdigest()
        with store.transaction() as tx:
            attempt=tx.get('attempt',key) or {'id':key,'at':stamp(),'count':0}
            if (now()-datetime.fromisoformat(attempt['at'])).total_seconds()>300:attempt={'id':key,'at':stamp(),'count':0}
            if attempt['count']>=10:fail(429,'rate_limited','Too many attempts. Try again in five minutes')
            user=next((u for u in tx.all('user') if u['username'].lower()==str(data.get('username','')).lower()),None)
            if not user or not user['enabled'] or not password_valid(data.get('password'),user['password']):
                # Store the failure before raising outside this transaction.
                attempt['count']+=1;tx.put('attempt',attempt)
                valid=False
            else:valid=True
            if valid:
                return login_response(tx,user)
        fail(401,'invalid_login','Invalid username or password')

    @app.post('/api/logout')
    def logout(request:Request):
        with store.transaction() as tx:
            require(request,tx);_,session=session_lookup(tx,request.cookies.get('console_session'));tx.remove('session',session['id'])
        response=JSONResponse({'ok':True});response.delete_cookie('console_session');return response

    @app.patch('/api/me')
    def me(request:Request,data:dict):
        with store.transaction() as tx:
            user=require(request,tx)
            if data.get('locale') not in {'en','zh-CN'}:raise ValueError('Unsupported language')
            user['locale']=data['locale'];tx.put('user',user);return public_user(user)

    @app.post('/api/me/password')
    def change_password(request:Request,data:dict):
        with store.transaction() as tx:
            user=require(request,tx)
            if not password_valid(data.get('current_password'),user['password']):fail(400,'invalid_password','Current password is incorrect')
            user['password']=password_hash(data.get('password'));tx.put('user',user);revoke(tx,user['id']);audit(tx,user,'password.change',user['id'])
        return {'ok':True}

    @app.get('/api/scripts')
    def scripts(request:Request):
        with store.transaction() as tx:
            user=require(request,tx)
            return [show_script(tx,s,user['role']=='admin') for s in tx.all('script') if user['role']=='admin' or not s['archived']]

    @app.get('/api/scripts/{sid}')
    def script_detail(sid:str,request:Request):
        with store.transaction() as tx:
            require(request,tx,True);return show_script(tx,get(tx,'script',sid),True,True)

    @app.post('/api/scripts')
    def script_create(request:Request,data:dict):
        with store.transaction() as tx:
            user=require(request,tx,True)
            name=data.get('name','')
            if not isinstance(name,str):raise ValueError('Script name must be text')
            name=name.strip()
            if not name or len(name)>100:raise ValueError('Script name must be 1–100 characters')
            source=data.get('source','')
            if not isinstance(source,str) or len(source.encode())>1048576:raise ValueError('Python source exceeds 1 MiB')
            manifest=validate_manifest(data.get('manifest',{}))
            script={'id':uid(),'name':name,'description':str(data.get('description',''))[:2000],'source':source,'manifest':manifest,'requirements':str(data.get('requirements','')),'archived':False,'default_version':None,'created_at':stamp(),'draft_dir':None}
            tx.put('script',script);audit(tx,user,'script.create',script['id']);return show_script(tx,script,True,True)

    @app.post('/api/scripts/upload')
    async def script_upload(request:Request,name:str=Form(...),description:str=Form(''),manifest:str=Form('{}'),file:UploadFile=File(...)):
        with store.transaction() as tx:require(request,tx,True)
        data=await file.read(20*1048576+1)
        if len(data)>20*1048576:raise ValueError('Upload exceeds 20 MiB')
        from .runtime import prepare_bundle
        sid=uid();folder=store.path/'scripts'/f'draft-{sid}'
        try:
            info=prepare_bundle(data,file.filename or '',folder)
            parsed=validate_manifest(json.loads(manifest))
            if not name.strip() or len(name)>100:raise ValueError('Script name must be 1–100 characters')
            source=(folder/'main.py').read_text(encoding='utf-8')
            script={'id':sid,'name':name.strip(),'description':description[:2000],'source':source,'manifest':parsed,'requirements':(folder/'requirements.txt').read_text() if (folder/'requirements.txt').exists() else '', 'archived':False,'default_version':None,'created_at':stamp(),'draft_dir':str(folder),'bundle_mode':info['mode']}
            with store.transaction() as tx:
                user=require(request,tx,True);tx.put('script',script);audit(tx,user,'script.upload',sid);return show_script(tx,script,True,True)
        except Exception:
            shutil.rmtree(folder,ignore_errors=True);raise

    @app.patch('/api/scripts/{sid}')
    def script_update(sid:str,request:Request,data:dict):
        with store.transaction() as tx:
            user=require(request,tx,True);s=get(tx,'script',sid)
            for key in ('name','description','source','requirements'):
                if key in data:
                    if not isinstance(data[key],str):raise ValueError('Script fields must be text')
                    s[key]=data[key]
            if not s['name'].strip() or len(s['name'])>100 or len(s['source'].encode())>1048576:raise ValueError('Invalid script name or source size')
            if 'manifest' in data:s['manifest']=validate_manifest(data['manifest'])
            tx.put('script',s);audit(tx,user,'script.update',sid);return show_script(tx,s,True,True)

    @app.post('/api/scripts/{sid}/check')
    def script_check(sid:str,request:Request):
        from .runtime import inspect_source
        with store.transaction() as tx:
            require(request,tx,True);s=get(tx,'script',sid);validate_manifest(s['manifest'])
            result=inspect_source(s['source'])
            if s.get('bundle_mode'):result['mode']=s['bundle_mode']
            return {'ok':True,**result}

    @app.post('/api/scripts/{sid}/publish')
    def script_publish(sid:str,request:Request):
        from .runtime import inspect_source, build_environment
        with store.transaction() as tx:
            user=require(request,tx,True);s=get(tx,'script',sid)
            if s['archived']:raise ValueError('Restore the script before publishing')
            info=inspect_source(s['source']);validate_manifest(s['manifest'])
            if s.get('bundle_mode'):info['mode']=s['bundle_mode']
            versions=[v for v in tx.all('version') if v['script_id']==sid]
            if any(v['status']=='building' for v in versions):fail(409,'conflict','A version is already building')
            vid=uid();number=max([v['number'] for v in versions]+[0])+1
            v={'id':vid,'script_id':sid,'number':number,'status':'building','mode':info['mode'],'manifest':s['manifest'],'created_at':stamp(),'build_log':'Preparing dependencies','freeze':''}
            tx.put('version',v)
        folder=store.path/'scripts'/vid
        try:
            if s.get('draft_dir'):shutil.copytree(s['draft_dir'],folder)
            else:folder.mkdir()
            (folder/'main.py').write_text(s['source'],encoding='utf-8')
            (folder/'requirements.txt').write_text(s.get('requirements',''),encoding='utf-8')
            runtime=build_environment(folder,store.path/'runtimes'/vid)
            digest=hashlib.sha256()
            for path in sorted(folder.rglob('*')):
                if path.is_file():digest.update(str(path.relative_to(folder)).encode());digest.update(path.read_bytes())
            v.update(status='published',runtime=runtime,build_log=runtime.get('log',''),freeze=runtime.get('freeze',''),digest=digest.hexdigest())
        except Exception as exc:
            v.update(status='failed',build_log=str(exc)[:20000])
        with store.transaction() as tx:
            tx.put('version',v);current=get(tx,'script',sid)
            if v['status']=='published':current['default_version']=vid;tx.put('script',current)
            audit(tx,user,'script.publish',vid)
        return v

    @app.post('/api/scripts/{sid}/archive')
    def script_archive(sid:str,request:Request,data:dict):
        with store.transaction() as tx:
            user=require(request,tx,True);s=get(tx,'script',sid);archived=bool(data.get('archived',True))
            if archived and any(t['script_id']==sid and not t['archived'] for t in tx.all('task')):fail(409,'referenced','Archive referencing tasks first')
            if archived and any(r['script_id']==sid and r['status'] in ACTIVE for r in tx.all('execution')):fail(409,'referenced','Wait for active executions to finish')
            s['archived']=archived;tx.put('script',s);audit(tx,user,'script.archive',sid);return show_script(tx,s,True,True)

    @app.post('/api/scripts/{sid}/default')
    def script_default(sid:str,request:Request,data:dict):
        with store.transaction() as tx:
            user=require(request,tx,True);s=get(tx,'script',sid);v=get(tx,'version',data.get('version_id',''))
            if v['script_id']!=sid or v['status']!='published':raise ValueError('Choose an available version of this script')
            s['default_version']=v['id'];tx.put('script',s);audit(tx,user,'script.default',v['id']);return show_script(tx,s,True,True)

    @app.post('/api/scripts/{sid}/retire')
    def script_retire(sid:str,request:Request,data:dict):
        with store.transaction() as tx:
            user=require(request,tx,True);s=get(tx,'script',sid);v=get(tx,'version',data.get('version_id',''))
            if v['script_id']!=sid:raise ValueError('Version does not belong to this script')
            if any(t['version_id']==v['id'] and not t['archived'] for t in tx.all('task')) or any(r['version_id']==v['id'] and r['status'] in ACTIVE for r in tx.all('execution')):fail(409,'referenced','Version is still referenced')
            if s['default_version']==v['id']:fail(409,'referenced','Choose another default version first')
            v['status']='retired';tx.put('version',v);audit(tx,user,'script.retire',v['id']);return v

    @app.get('/api/tasks')
    def tasks(request:Request):
        with store.transaction() as tx:
            require(request,tx);settings=tx.get('meta','settings');return [show_task(t,settings) for t in tx.all('task')]

    @app.post('/api/tasks')
    def task_create(request:Request,data:dict):
        with store.transaction() as tx:
            user=require(request,tx);t=task_data(tx,data);tx.put('task',t);audit(tx,user,'task.create',t['id']);return show_task(t,tx.get('meta','settings'))

    @app.get('/api/tasks/{tid}')
    def task_detail(tid:str,request:Request):
        with store.transaction() as tx:
            require(request,tx);return show_task(get(tx,'task',tid),tx.get('meta','settings'))

    @app.put('/api/tasks/{tid}')
    def task_update(tid:str,request:Request,data:dict):
        with store.transaction() as tx:
            user=require(request,tx);old=get(tx,'task',tid);t=task_data(tx,data,old);tx.put('task',t);audit(tx,user,'task.update',tid);return show_task(t,tx.get('meta','settings'))

    @app.post('/api/tasks/{tid}/toggle')
    def task_toggle(tid:str,request:Request,data:dict):
        with store.transaction() as tx:
            user=require(request,tx);t=get(tx,'task',tid)
            if t['archived']:raise ValueError('Restore task before enabling')
            updated=task_data(tx,{**t,'enabled':bool(data.get('enabled'))},t);tx.put('task',updated);audit(tx,user,'task.toggle',tid);return show_task(updated,tx.get('meta','settings'))

    @app.post('/api/tasks/{tid}/archive')
    def task_archive(tid:str,request:Request,data:dict):
        with store.transaction() as tx:
            user=require(request,tx);t=get(tx,'task',tid);archived=bool(data.get('archived',True))
            if not archived:
                version=tx.get('version',t['version_id'])
                if not version or version['status']!='published':fail(409,'retired_version','Choose a published script version before restoring this task')
                script=tx.get('script',t['script_id'])
                if not script or script['archived']:fail(409,'archived_script','Restore the script before restoring this task')
            t.update(archived=archived,enabled=False,next_run=None,revision=t['revision']+1);tx.put('task',t);audit(tx,user,'task.archive',tid);return show_task(t,tx.get('meta','settings'))

    @app.post('/api/tasks/{tid}/run')
    def task_run(tid:str,request:Request):
        with store.transaction() as tx:
            user=require(request,tx);t=get(tx,'task',tid)
            if t['archived']:raise ValueError('Restore task before running')
            key=request.headers.get('idempotency-key','')
            if not key or len(key)>200:raise ValueError('An Idempotency-Key header is required')
            r=enqueue(tx,t,'manual',user['username'],f"manual:{user['id']}:{tid}:{key}");audit(tx,user,'task.run',r['id']);return show_execution(r)

    @app.post('/api/preview')
    def preview(request:Request,data:dict):
        with store.transaction() as tx:require(request,tx)
        return {'times':[d.isoformat() for d in next_runs(data.get('schedule',{}),data.get('timezone','UTC'),now())]}

    @app.get('/api/executions')
    def executions(request:Request,task_id:str='',status:str='',trigger:str='',start:str='',end:str='',page:int=1):
        with store.transaction() as tx:
            require(request,tx);rows=tx.all('execution')
        for field,value in (('task_id',task_id),('status',status),('trigger',trigger)):
            if value:rows=[r for r in rows if r[field]==value]
        if start:rows=[r for r in rows if r['created_at']>=start]
        if end:rows=[r for r in rows if r['created_at']<=end]
        rows.sort(key=lambda r:r['created_at'],reverse=True);page=max(1,page)
        return {'items':[show_execution(row) for row in rows[(page-1)*50:page*50]],'total':len(rows),'page':page}

    @app.get('/api/executions/{rid}')
    def execution_detail(rid:str,request:Request):
        with store.transaction() as tx:require(request,tx);return show_execution(get(tx,'execution',rid))

    @app.get('/api/executions/{rid}/logs')
    def logs(rid:str,request:Request,stdout_offset:int=0,stderr_offset:int=0):
        with store.transaction() as tx:require(request,tx);run=get(tx,'execution',rid)
        result={'truncated':run['logs_truncated'],'expired':run['logs_expired']}
        for stream,offset in (('stdout',stdout_offset),('stderr',stderr_offset)):
            path=store.path/'runs'/rid/(stream+'.txt')
            content=path.read_text(encoding='utf-8',errors='replace') if path.exists() else ''
            result[stream]=content[max(0,offset):];result[stream+'_offset']=len(content)
        return result

    @app.post('/api/executions/{rid}/cancel')
    def execution_cancel(rid:str,request:Request):
        with store.transaction() as tx:
            user=require(request,tx);run=get(tx,'execution',rid)
            if run['status']=='queued':run.update(status='cancelled',finished_at=stamp(),reason='cancelled')
            elif run['status']=='running':run['status']='cancelling'
            tx.put('execution',run);audit(tx,user,'execution.cancel',rid);return show_execution(run)

    @app.get('/api/executions/{rid}/artifacts/{name:path}')
    def artifact(rid:str,name:str,request:Request):
        with store.transaction() as tx:require(request,tx);run=get(tx,'execution',rid)
        if run['logs_expired']:fail(410,'expired','Output has expired')
        if name not in {a['name'] for a in run['artifacts']}:fail(404,'not_found','Output not found')
        root=store.path/'runs'/rid/'outputs';path=root/name
        if any(p.is_symlink() for p in [path,*path.parents]) or not path.resolve().is_relative_to(root.resolve()) or not path.is_file():fail(404,'not_found','Output not found')
        return FileResponse(path,filename=path.name,media_type='application/octet-stream')

    @app.post('/internal/tick')
    def dispatch(request:Request):
        if not hmac.compare_digest(request.headers.get('x-dispatch-token',''),(store.path/'dispatch-token').read_text().strip()):fail(403,'forbidden','Invalid dispatch token')
        return tick(store)

    @app.get('/api/admin/settings')
    def settings_get(request:Request):
        with store.transaction() as tx:
            require(request,tx,True);s=tx.get('meta','settings');return {**s,'scheduler':scheduler(s)}

    @app.patch('/api/admin/settings')
    def settings_update(request:Request,data:dict):
        with store.transaction() as tx:
            user=require(request,tx,True);s=tx.get('meta','settings')
            for key,maximum in (('concurrency',16),('log_retention_days',3650),('metadata_retention_days',3650)):
                if key in data:
                    if type(data[key]) is not int or not 1<=data[key]<=maximum:raise ValueError('Setting is outside allowed range')
                    s[key]=data[key]
            if 'timezone' in data:
                from zoneinfo import ZoneInfo
                try:ZoneInfo(data['timezone'])
                except Exception:raise ValueError('Invalid IANA timezone')
                s['timezone']=data['timezone']
            if s['metadata_retention_days']<s['log_retention_days']:raise ValueError('Metadata retention cannot be shorter than log retention')
            tx.put('meta',s);audit(tx,user,'settings.update','settings');return {**s,'scheduler':scheduler(s)}

    @app.get('/api/admin/users')
    def users(request:Request):
        with store.transaction() as tx:require(request,tx,True);return [public_user(u) for u in tx.all('user')]

    @app.post('/api/admin/users')
    def user_create(request:Request,data:dict):
        with store.transaction() as tx:
            actor=require(request,tx,True);u=new_user(tx,data);audit(tx,actor,'user.create',u['id']);return public_user(u)

    @app.patch('/api/admin/users/{user_id}')
    def user_update(user_id:str,request:Request,data:dict):
        with store.transaction() as tx:
            actor=require(request,tx,True);u=get(tx,'user',user_id)
            role=data.get('role',u['role']);enabled=bool(data.get('enabled',u['enabled']))
            if role not in {'admin','operator'}:raise ValueError('Invalid role')
            if u['role']=='admin' and u['enabled'] and (not enabled or role!='admin') and len([x for x in tx.all('user') if x['role']=='admin' and x['enabled']])<=1:fail(409,'last_admin','The last administrator must remain enabled')
            u.update(role=role,enabled=enabled)
            if data.get('password'):u['password']=password_hash(data['password'])
            tx.put('user',u);revoke(tx,user_id);audit(tx,actor,'user.update',user_id);return public_user(u)

    @app.get('/api/admin/variables')
    def variables(request:Request):
        with store.transaction() as tx:require(request,tx,True);return [{k:v for k,v in x.items() if k!='encrypted'} for x in tx.all('variable')]

    @app.post('/api/admin/variables')
    def variable_set(request:Request,data:dict):
        with store.transaction() as tx:
            user=require(request,tx,True);name=data.get('name','');scope=data.get('scope','instance');value=data.get('value')
            if not valid_variable_name(name):raise ValueError('Invalid or reserved variable name')
            if not isinstance(value,str) or len(value)>65536 or '\0' in value:raise ValueError('Invalid variable value')
            if scope!='instance':get(tx,'script',scope)
            key=hashlib.sha256((scope+':'+name).encode()).hexdigest()
            v={'id':key,'name':name,'scope':scope,'encrypted':store.fernet.encrypt(value.encode()).decode(),'updated_at':stamp()}
            tx.put('variable',v);audit(tx,user,'variable.set',key);return {k:x for k,x in v.items() if k!='encrypted'}

    @app.delete('/api/admin/variables/{key}')
    def variable_delete(key:str,request:Request):
        with store.transaction() as tx:user=require(request,tx,True);get(tx,'variable',key);tx.remove('variable',key);audit(tx,user,'variable.delete',key)
        return {'ok':True}

    @app.get('/api/admin/audit')
    def audits(request:Request):
        with store.transaction() as tx:require(request,tx,True);return sorted(tx.all('audit'),key=lambda a:a['created_at'],reverse=True)[:200]

    static=Path(__file__).parent/'static'
    app.mount('/static',StaticFiles(directory=static),name='static')
    @app.get('/{path:path}')
    def page(path:str):
        if path.startswith(('api/','internal/')):fail(404,'not_found','Not found')
        return FileResponse(static/'index.html')
    return app
