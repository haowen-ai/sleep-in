"""Versioned native runtime packs. Dependency installation occurs only during build."""
import copy
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import threading
from .store import uid,stamp
from .workflows_projects import SourceProjects,relative_path,file_hash,manifest_tree
from .workflows_runtime import executable,run_script

PACK_LANGUAGES={'python','javascript','shell','java','c','cpp','custom'}


def run_build(argv,directory,log,env=None,timeout=600):
    result=subprocess.run(argv,cwd=directory,env=env,capture_output=True,text=True,timeout=timeout)
    log.append('$ '+json.dumps(argv)+'\n'+result.stdout+result.stderr)
    if result.returncode:raise ValueError('Build command failed: '+(result.stderr or result.stdout)[-3000:])
    return result.stdout


def safe_files(directory,files):
    if not isinstance(files,dict):raise ValueError('shared_files must be a named text object')
    for name,text in files.items():
        if not isinstance(text,str):raise ValueError('Shared modules must be text')
        target=Path(directory)/relative_path(name);target.parent.mkdir(parents=True,exist_ok=True);target.write_text(text)


def pack_public(record):return {k:v for k,v in record.items() if k not in {'directory','executable','compiler','java','config','manifest'}}


class RuntimePacks:
    def __init__(self,store):self.store=store
    def _get(self,kind,key):
        with self.store.transaction() as tx:record=tx.get(kind,key)
        if not record:raise ValueError('Runtime profile/version not found')
        return record
    def list(self):
        with self.store.transaction() as tx:profiles=tx.all('runtime_profile')
        return [self.get(p['id']) for p in profiles]
    def get(self,pid):
        profile=self._get('runtime_profile',pid)
        with self.store.transaction() as tx:versions=[v for v in tx.all('runtime_version') if v['profile_id']==pid]
        profile['versions']=[self.version(v['id']) for v in sorted(versions,key=lambda v:v['number'])]
        return profile
    def version(self,vid,private=False):
        record=self._get('runtime_version',vid)
        with self.store.transaction() as tx:publications=tx.all('workflow_version')
        record['referencing_workflows']=sorted({v['workflow_id'] for v in publications if any(n.get('config',{}).get('runtime_version_id')==vid for n in v.get('snapshot',{}).get('nodes',[]))})
        return record if private else {**pack_public(record),'config':record['config']}
    def create(self,data):
        language=data.get('language')
        if language not in PACK_LANGUAGES:raise ValueError('Unsupported runtime language')
        profile={'id':uid(),'name':str(data.get('name',language))[:200],'language':language,'created_at':stamp(),'latest_version_id':None}
        with self.store.transaction() as tx:tx.put('runtime_profile',profile)
        self.new_version(profile['id'],data.get('config',{}))
        return self.get(profile['id'])
    def new_version(self,pid,config):
        if not isinstance(config,dict):raise ValueError('Runtime config must be an object')
        json.dumps(config,allow_nan=False)
        with self.store.transaction() as tx:
            profile=tx.get('runtime_profile',pid)
            if not profile:raise ValueError('Runtime profile not found')
            versions=[v for v in tx.all('runtime_version') if v['profile_id']==pid]
            version={'id':uid(),'profile_id':pid,'number':len(versions)+1,'language':profile['language'],'config':copy.deepcopy(config),'status':'draft','created_at':stamp(),'platform':platform.system().lower(),'architecture':platform.machine(),'build_log':'','verification':{'status':'not_run'},'dependency_summary':None}
            tx.put('runtime_version',version);profile['latest_version_id']=version['id'];tx.put('runtime_profile',profile)
        return self.version(version['id'])
    def build(self,pid,vid=None):
        profile=self._get('runtime_profile',pid);vid=vid or profile['latest_version_id']
        with self.store.transaction() as tx:
            record=tx.get('runtime_version',vid)
            if not record or record['profile_id']!=pid:raise ValueError('Version does not belong to profile')
            if record['status'] in {'ready','building'}:raise ValueError('Runtime version is immutable or already building')
            record.update(status='building',started_at=stamp());tx.put('runtime_version',record)
        log=[];root=self.store.path/'runtime-packs'/vid;root.mkdir(parents=True,exist_ok=True)
        config=record['config'];language=record['language']
        try:
            if language=='python' and config.get('requirements_lock'):
                lock=config['requirements_lock'].replace('\\\n',' ')
                requirements=[line.strip() for line in lock.splitlines() if line.strip() and not line.lstrip().startswith('#')]
                if any('==' not in line or '--hash=sha256:' not in line or line.startswith('-') for line in requirements):raise ValueError('Every Python dependency requires an exact version and SHA256 hash')
            tools=self._resolve_tools(language,config)
            record.update(tools)
            shared=root/'shared';shared.mkdir(exist_ok=True);safe_files(shared,config.get('shared_files',{}))
            env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1','PIP_DISABLE_PIP_VERSION_CHECK':'1','PIP_NO_INPUT':'1'}
            if language=='python':
                base=tools['executable'];run_build([base,'-m','venv','--copies',str(root/'env')],root,log,env)
                record['executable']=str(root/'env'/'bin'/'python')
                lock=config.get('requirements_lock','');(root/'requirements.lock').write_text(lock)
                if lock.strip():
                    args=[record['executable'],'-m','pip','install','--require-hashes','--no-deps','-r',str(root/'requirements.lock')]
                    if config.get('wheelhouse'):args+=['--no-index','--find-links',str(Path(config['wheelhouse']).resolve())]
                    run_build(args,root,log,env,config.get('build_timeout',600))
                run_build([record['executable'],'-m','pip','check'],root,log,env)
                record['dependency_summary']=run_build([record['executable'],'-m','pip','freeze','--all'],root,log,env)
            elif language=='javascript':
                copied=root/'bin'/'node';copied.parent.mkdir(exist_ok=True);shutil.copy2(tools['executable'],copied);record['executable']=str(copied)
                package=config.get('package_json',{'name':'sleep-in-runtime','version':'1.0.0','private':True})
                if not isinstance(package,dict):raise ValueError('package_json must be an object')
                (root/'package.json').write_text(json.dumps(package))
                lock=config.get('package_lock')
                if any(package.get(k) for k in ('dependencies','devDependencies','optionalDependencies')) and not lock:raise ValueError('npm dependencies require package_lock')
                if lock:
                    if not isinstance(lock,dict) or not lock.get('lockfileVersion'):raise ValueError('Invalid npm lockfile')
                    for key,entry in lock.get('packages',{}).items():
                        if key and entry.get('resolved','').startswith(('http:','https:')) and not entry.get('integrity'):raise ValueError('npm lock entries require integrity hashes')
                    (root/'package-lock.json').write_text(json.dumps(lock))
                    npm=config.get('npm_executable') or shutil.which('npm')
                    if not npm:
                        candidate=Path(tools['executable']).parent/'npm'
                        if candidate.exists():npm=str(candidate)
                    if not npm:raise ValueError('npm is required to build dependency packs')
                    env['PATH']=str(Path(tools['executable']).parent)+os.pathsep+env.get('PATH','')
                    run_build([npm,'ci','--ignore-scripts','--no-audit','--no-fund'],root,log,env,config.get('build_timeout',600))
                record['dependency_summary']=json.dumps(package.get('dependencies',{}),sort_keys=True)
            elif language=='custom':
                if not config.get('run_argv') or not config.get('self_test_source'):raise ValueError('Custom runtimes require run_argv and a protocol self_test_source')
                record['dependency_summary']='Owner-managed custom argv pack'
            else:record['dependency_summary']='Native standard toolchain'
            record['directory']=str(root)
            record['module_format']=config.get('module_format','cjs')
            self._self_test(record,root,log)
            record['executable_sha256']=file_hash(record['executable'])
            if record.get('java'):record['java_sha256']=file_hash(record['java'])
            record['manifest']=manifest_tree(root)
            record['digest']=hashlib.sha256(json.dumps({'config':config,'files':record['manifest'],'tools':tools,'platform':record['platform'],'architecture':record['architecture']},sort_keys=True).encode()).hexdigest()
            record.update(status='ready',verification={'status':'passed','protocol':1,'tested_at':stamp()},finished_at=stamp())
        except Exception as exc:
            log.append(str(exc));record.update(status='failed',verification={'status':'failed','message':str(exc)},finished_at=stamp())
        record['build_log']='\n'.join(log)[-200000:]
        with self.store.transaction() as tx:tx.put('runtime_version',record)
        return self.version(vid)
    def _resolve_tools(self,language,config):
        if config.get('toolchain_id'):
            with self.store.transaction() as tx:toolchain=tx.get('runtime_toolchain',config['toolchain_id'])
            if not toolchain or toolchain['status']!='ready':raise ValueError('Install a ready toolchain first')
            home=Path(toolchain['home'])
            names={'java':'javac','javascript':'node','python':'python','c':'clang','cpp':'clang++','shell':'bash'}
            path=toolchain.get('executables',{}).get(language) or str(home/'bin'/names.get(language,language))
        else:path=config.get('executable') or executable(language)
        if language=='custom':path=config.get('run_argv',[''])[0]
        resolved=shutil.which(path or '')
        if not resolved:raise ValueError('Runtime executable unavailable')
        output=subprocess.run([resolved,'-version' if language=='java' else '--version'],capture_output=True,text=True,timeout=10)
        if output.returncode and language!='custom':raise ValueError('Toolchain is unavailable: '+(output.stderr or output.stdout)[:500])
        result={'executable':resolved,'tool_version':(output.stdout+output.stderr)[:1000],'tool_sha256':file_hash(resolved)}
        if language in {'java','c','cpp'}:result['compiler']=resolved
        if language=='java':
            result['java']=str(Path(resolved).parent/'java')
            if not Path(result['java']).is_file():raise ValueError('Java launcher missing from JDK')
        return result
    def _self_test(self,record,root,log):
        language=record['language'];config=record['config'];expected={'ok':True}
        if language=='python':source='def main(inputs): return {"ok": inputs["ok"]}'
        elif language=='javascript':source='function main(inputs){return {ok:inputs.ok}}'
        elif language=='shell':source='printf \'{"schemaVersion":1,"data":{"ok":true},"artifacts":[]}\' > "$SLEEP_IN_OUTPUT_FILE"'
        elif language=='java':source='import java.nio.file.*; public class Main {public static void main(String[] args) throws Exception {Files.writeString(Path.of(System.getenv("SLEEP_IN_OUTPUT_FILE")),"{\\"schemaVersion\\":1,\\"data\\":{\\"ok\\":true},\\"artifacts\\":[]}");}}'
        elif language in {'c','cpp'}:source='#include <stdio.h>\n#include <stdlib.h>\nint main(void){FILE*f=fopen(getenv("SLEEP_IN_OUTPUT_FILE"),"w");if(!f)return 1;fputs("{\\"schemaVersion\\":1,\\"data\\":{\\"ok\\":true},\\"artifacts\\":[]}",f);return fclose(f);}'
        else:source=config['self_test_source']
        node={'id':'selftest','kind':language,'source':source,'config':{'entry_mode':'function' if language in {'python','javascript'} else 'file'},'_runtime':record}
        if language in {'java','c','cpp','custom'}:
            from .workflows_projects_build import build_project_node
            node=build_project_node(self.store,node)
        result=run_script(node,expected,root/'self-test',self.store.path)
        if result['status']!='succeeded' or result.get('output',{}).get('data')!=expected:raise ValueError('Runtime protocol self-test failed: '+json.dumps(result,default=str)[-2000:])
        log.append('JSON file protocol self-test passed')


def prepare_node(store,node):
    from .workflows_projects_build import build_project_node
    result=copy.deepcopy(node);language=result['kind']
    if language=='sql':return result
    config=result.setdefault('config',{});packs=RuntimePacks(store);vid=config.get('runtime_version_id')
    if vid:version=packs.version(vid,private=True)
    else:
        with store.transaction() as tx:
            versions=[v for v in tx.all('runtime_version') if v.get('builtin')==language and v['status']=='ready']
        if versions:version=versions[-1]
        else:
            profile=packs.create({'name':'Built-in '+language,'language':language,'config':{}});version=packs.build(profile['id'])
            if version['status']=='ready':
                with store.transaction() as tx:
                    version=tx.get('runtime_version',version['id']);version['builtin']=language;tx.put('runtime_version',version)
            else:raise ValueError(version['build_log'])
    if version['status']!='ready':raise ValueError('Runtime version is not ready')
    if version['language']!=language:raise ValueError('Runtime language does not match node')
    if version['platform']!=platform.system().lower() or version['architecture']!=platform.machine():raise ValueError('Runtime platform/architecture does not match this worker')
    config['runtime_version_id']=version['id'];result['_runtime']=version
    if config.get('project_id'):
        project=SourceProjects(store).get(config['project_id'],private=True)
        if project['language']!=language:raise ValueError('Project language does not match node')
        for name,digest in project['manifest'].items():
            if file_hash(Path(project['directory'])/name)!=digest:raise ValueError('Immutable source project changed')
        result['_project']=project
    result=build_project_node(store,result)
    from .workflows_runtime import verify_frozen_node
    verify_frozen_node(result)
    return result


def register_runtime_routes(app,store,require):
    from fastapi import Request,HTTPException
    from .workflows_toolchains import Toolchains
    packs=RuntimePacks(store);projects=SourceProjects(store)
    def authorized(request):
        with store.transaction() as tx:return require(request,tx,admin=True)
    def invoke(fn,*args):
        try:return fn(*args)
        except (ValueError,KeyError,TypeError) as exc:raise HTTPException(400,detail={'code':'runtime_invalid','message':str(exc)}) from exc
    @app.get('/api/runtime-profiles')
    def profiles(request:Request):authorized(request);return packs.list()
    @app.post('/api/runtime-profiles')
    async def create(request:Request):authorized(request);return invoke(packs.create,await request.json())
    @app.get('/api/runtime-profiles/{pid}')
    def profile(pid:str,request:Request):authorized(request);return invoke(packs.get,pid)
    @app.post('/api/runtime-profiles/{pid}/versions')
    async def version(pid:str,request:Request):authorized(request);return invoke(packs.new_version,pid,(await request.json()).get('config',{}))
    @app.post('/api/runtime-profiles/{pid}/build')
    async def build_route(pid:str,request:Request):
        authorized(request);data=await request.json()
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(invoke,packs.build,pid,data.get('version_id'))
    @app.get('/api/runtime-versions/{vid}')
    def runtime_version(vid:str,request:Request):authorized(request);return invoke(packs.version,vid)
    @app.get('/api/source-projects')
    def project_list(request:Request):authorized(request);return projects.list()
    @app.post('/api/source-projects')
    async def project_create(request:Request):authorized(request);return invoke(projects.create,await request.json())
    @app.post('/api/source-projects/upload')
    async def upload(request:Request):
        authorized(request);form=await request.form();file=form.get('file')
        if not file or not hasattr(file,'read'):raise HTTPException(400,detail={'code':'runtime_invalid','message':'File required'})
        payload=await file.read(100*1024*1024+1)
        return invoke(projects.upload,form.get('name','Project'),form.get('language'),form.get('entrypoint'),payload,file.filename,json.loads(form.get('config','{}')))
    @app.get('/api/source-projects/{pid}')
    def project(pid:str,request:Request):authorized(request);return invoke(projects.get,pid)
    @app.get('/api/runtime-toolchains')
    def toolchains(request:Request):authorized(request);return Toolchains(store).list()
    @app.post('/api/runtime-toolchains/install')
    async def install(request:Request):
        authorized(request);data=await request.json()
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(invoke,Toolchains(store).install,data.get('manifest_id'))
