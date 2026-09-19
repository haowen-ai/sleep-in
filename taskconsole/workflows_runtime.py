"""Trusted native language workers with an explicit JSON/file boundary."""
import hashlib
import math
import platform
import json
import os
from pathlib import Path
import shutil
import signal
import sqlite3
import subprocess
import sys
import time
from .workflows_build_lock import subprocess_lock_options

LANGUAGES = ('python','javascript','shell','sql','java','c','cpp','custom')


class WorkerOutputError(ValueError):
    """Output collection failed after the worker produced diagnostic logs."""
    def __init__(self,message,logs):
        super().__init__(message)
        self.logs=logs


def executable(language):
    names = {'python':os.environ.get('SLEEP_IN_PYTHON',sys.executable), 'javascript':os.environ.get('SLEEP_IN_NODE','node'), 'shell':'bash', 'java':'javac','c':'cc','cpp':'c++'}
    return shutil.which(names.get(language,''))


def runtimes():
    result = []
    for lang in LANGUAGES:
        path = executable(lang)
        ready = lang=='sql' or bool(path)
        version = sqlite3.sqlite_version if lang=='sql' else ''
        if path:
            try:
                proc = subprocess.run([path,'-version' if lang=='java' else '--version'], capture_output=True,text=True,timeout=5)
                version = (proc.stdout or proc.stderr).splitlines()[0][:200]
                ready = proc.returncode==0
            except Exception as exc: version=str(exc);ready=False
        result.append({'id':'native_'+lang,'language':lang,'name':'Native '+lang,'status':'ready' if ready else 'unavailable','executable':path,'version':version,'reason':None if ready else f'Install a working {lang} toolchain and restart the service','capabilities':{'file_protocol':True,'cpu_memory_isolation':False}})
    return result


def build(node, root):
    lang = node['kind']
    if lang not in {'java','c','cpp'}: return None
    source = node.get('source','')
    compiler = executable(lang)
    if not compiler: raise ValueError(f'{lang}: compiler unavailable')
    main = node.get('config',{}).get('main_class','Main')
    if not main.isidentifier(): raise ValueError('Invalid Java main class')
    compiler_check=subprocess.run([compiler,'-version' if lang=='java' else '--version'],capture_output=True,text=True,timeout=10)
    if compiler_check.returncode:raise ValueError(f'{lang}: compiler self-check failed')
    identity={'language':lang,'source':source,'compiler':compiler,'version':compiler_check.stdout+compiler_check.stderr,'main':main,'platform':platform.platform(),'architecture':platform.machine(),'standard':'c++17' if lang=='cpp' else 'c11' if lang=='c' else 'java'}
    digest = hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    directory = Path(root)/'workflow-builds'/digest
    directory.mkdir(parents=True,exist_ok=True)
    target = directory/('program' if lang!='java' else main+'.class')
    if not target.exists():
        file = directory/(main+'.java' if lang=='java' else 'main.'+('cpp' if lang=='cpp' else 'c'))
        file.write_text(source)
        argv = [compiler,str(file)] if lang=='java' else [compiler, '-std=c++17' if lang=='cpp' else '-std=c11', str(file),'-o',str(target)]
        proc = subprocess.run(argv,capture_output=True,text=True,timeout=60,cwd=directory)
        (directory/'build.txt').write_text(proc.stdout+proc.stderr)
        if proc.returncode: raise ValueError(f'{lang} build failed: {(proc.stdout+proc.stderr)[-4000:]}')
    if lang=='java':
        java = shutil.which('java')
        if not java: raise ValueError('Java launcher unavailable')
        return {'argv':[java,'-cp',str(directory),main],'digest':digest,'log':(directory/'build.txt').read_text(),'toolchain':identity,'artifact_sha256':hashlib.sha256(target.read_bytes()).hexdigest()}
    return {'argv':[str(target)],'digest':digest,'log':(directory/'build.txt').read_text(),'toolchain':identity,'artifact_sha256':hashlib.sha256(target.read_bytes()).hexdigest()}


def path_tokens(path):
    if path is None or path == "": return []
    if isinstance(path, str): return path.split('.')
    if not isinstance(path, list) or any(type(p) not in (str,int) or isinstance(p,int) and p<0 for p in path):
        raise ValueError('Path must contain keys and nonnegative indexes')
    return path


def resolve_path(obj,path):
    value=obj
    shorthand=isinstance(path,str)
    for part in path_tokens(path):
        if isinstance(value,list):
            if shorthand and isinstance(part,str) and part.isdigit(): part=int(part)
            if type(part) is not int or part<0: raise ValueError('Array index must be a nonnegative integer')
            value=value[part]
        elif isinstance(value,dict):
            if not isinstance(part,str): raise KeyError(str(part))
            value=value[part]
        else: raise KeyError(str(path))
    return value


SCHEMA_TYPES={'object':dict,'array':list,'string':str,'number':(int,float),'integer':int,'boolean':bool,'null':type(None)}


def validate_schema(schema):
    if not isinstance(schema,dict): raise ValueError('Schema must be an object')
    unsupported=set(schema)-{'type','properties','items','required','title','description','metadata'}
    if unsupported: raise ValueError('Unsupported schema keyword: '+','.join(sorted(unsupported)))
    expected=schema.get('type',[])
    variants=expected if isinstance(expected,list) else [expected]
    if any(not isinstance(v,str) or v not in SCHEMA_TYPES for v in variants): raise ValueError('Unsupported schema type')
    if 'required' in schema and (not isinstance(schema['required'],list) or any(not isinstance(v,str) for v in schema['required'])): raise ValueError('required must be field names')
    if 'properties' in schema:
        if not isinstance(schema['properties'],dict): raise ValueError('properties must be an object')
        for child in schema['properties'].values(): validate_schema(child)
    if 'items' in schema: validate_schema(schema['items'])


def check_schema(value,schema,path='output'):
    if not schema: return
    validate_schema(schema)
    expected=schema.get('type')
    variants=expected if isinstance(expected,list) else [expected] if expected else []
    if variants and not any(isinstance(value,SCHEMA_TYPES[v]) and not (v in {'number','integer'} and isinstance(value,bool)) for v in variants):
        raise ValueError(f'{path}: expected {expected}')
    if isinstance(value,dict):
        for key in schema.get('required',[]):
            if key not in value: raise ValueError(f'{path}: missing required {key}')
        for key,child in schema.get('properties',{}).items():
            if key in value: check_schema(value[key],child,path+'.'+key)
    if isinstance(value,list) and schema.get('items'):
        for index,item in enumerate(value): check_schema(item,schema['items'],path+f'[{index}]')


def portable(value):
    if isinstance(value,float) and not math.isfinite(value): raise ValueError('Non-finite JSON number')
    if type(value) is int and abs(value)>9007199254740991: raise ValueError('Unsafe integer; encode as a schema-marked string')
    if isinstance(value,dict):
        for child in value.values(): portable(child)
    if isinstance(value,list):
        for child in value: portable(child)


def verify_frozen_node(node):
    from .workflows_projects import file_hash,manifest_tree
    for key,label in [('_runtime','runtime'),('_project','source project'),('_build','compiled project')]:
        record=node.get(key,{})
        if not record.get('manifest') or not record.get('directory'):continue
        actual=manifest_tree(record['directory'])
        if key=='_build':actual.pop('build-result.json',None)
        if actual!=record['manifest']:raise ValueError('Immutable '+label+' content changed')
    runtime=node.get('_runtime',{})
    for key in ('executable','java'):
        if runtime.get(key+'_sha256') and file_hash(runtime[key])!=runtime[key+'_sha256']:raise ValueError('Immutable runtime executable changed')


def run_script(node,inputs,directory,root,cancelled=lambda:False):
    verify_frozen_node(node)
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    artifacts=directory/'artifacts';artifacts.mkdir(exist_ok=True)
    inp=directory/'input.json';out=directory/'output.json'
    inp.write_text(json.dumps(inputs,allow_nan=False))
    env={key:value for key,value in os.environ.items() if key in {'PATH','HOME','TMPDIR','LANG','SYSTEMROOT'}}
    env.update(SLEEP_IN_INPUT_FILE=str(inp),SLEEP_IN_OUTPUT_FILE=str(out),SLEEP_IN_ARTIFACT_DIR=str(artifacts),SLEEP_IN_RUN_ID=node.get('_execution',{}).get('run_id',directory.parent.parent.name),SLEEP_IN_NODE_ID=node['id'])
    lang=node['kind'];source=node.get('source','')
    profile=node.get('_runtime',{});project=node.get('_project')
    project_dir=directory/'project';project_dir.mkdir(exist_ok=True)
    shared=Path(profile['directory'])/'shared' if profile.get('directory') else None
    if shared and shared.exists():shutil.copytree(shared,project_dir,dirs_exist_ok=True)
    if project:
        shutil.copytree(project['directory'],project_dir,dirs_exist_ok=True)
        source=(project_dir/project['entrypoint']).read_text() if lang in {'python','javascript','shell'} else source
    if profile.get('directory') and (Path(profile['directory'])/'node_modules').exists():
        link=project_dir/'node_modules'
        if not link.exists():link.symlink_to(Path(profile['directory'])/'node_modules',target_is_directory=True)
    env['PYTHONDONTWRITEBYTECODE']='1'
    env['PYTHONPATH']=str(project_dir)+(os.pathsep+str((project_dir/project['entrypoint']).parent) if project else '')
    if profile.get('java'):env['JAVA_HOME']=str(Path(profile['java']).parent.parent)
    if profile.get('executable'):env['PATH']=str(Path(profile['executable']).parent)+os.pathsep+env.get('PATH','')
    if lang=='python':
        script=project_dir/'__sleepin_main.py'
        if project and node.get('config',{}).get('entry_mode')!='file':
            module='.'.join(Path(project['entrypoint']).with_suffix('').parts)
            source='import importlib\nmain=importlib.import_module('+repr(module)+').main'
        elif project:
            script=project_dir/project['entrypoint']
        script.write_text(source if node.get("config",{}).get("entry_mode")=="file" else source+'\n\nif __name__ == "__main__":\n import json,os,inspect,asyncio\n _result=main(json.load(open(os.environ["SLEEP_IN_INPUT_FILE"])))\n if inspect.isawaitable(_result): _result=asyncio.run(_result)\n if not isinstance(_result,dict): raise ValueError("main must return an object")\n _result={"schemaVersion":1,"data":_result,"artifacts":[]}\n with open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w") as _f: json.dump(_result,_f,allow_nan=False)\n')
        argv=[node.get('_runtime',{}).get('executable') or executable(lang),str(script)]
    elif lang=='javascript':
        esm=node.get('config',{}).get('module_format',profile.get('module_format','cjs'))=='esm' or bool(project and project['entrypoint'].endswith('.mjs'))
        script=(project_dir/project['entrypoint']).parent/('__sleepin_main.mjs' if esm else '__sleepin_main.cjs') if project else project_dir/('__sleepin_main.mjs' if esm else '__sleepin_main.cjs')
        helpers='import * as __sleepin_fs from "node:fs";\n' if esm else ''
        trailer='\n;(async()=>{'+('' if esm else 'const __sleepin_fs=require("fs");')+'const result=await (typeof main==="function"?main:module.exports.main)(JSON.parse(__sleepin_fs.readFileSync(process.env.SLEEP_IN_INPUT_FILE,"utf8")));if(!result || typeof result!=="object" || Array.isArray(result))throw Error("main must return an object");__sleepin_fs.writeFileSync(process.env.SLEEP_IN_OUTPUT_FILE,JSON.stringify({schemaVersion:1,data:result,artifacts:[]},(key,value)=>{if(typeof value==="number"&&!Number.isFinite(value))throw Error("Non-finite JSON number");if(typeof value==="undefined")throw Error("Undefined is not JSON data");return value}));})().catch(e=>{console.error(e);process.exit(1)});'
        if node.get('config',{}).get('entry_mode')=='file':script.write_text(source)
        elif esm:script.write_text(helpers+source+trailer)
        else:script.write_text(source+trailer)
        argv=[profile.get('executable') or executable(lang),str(script)]
    elif lang=='shell':
        script=project_dir/'main.sh';script.write_text(source);argv=[node.get('_runtime',{}).get('executable') or executable(lang),str(script)]
    else: argv=(node.get('_build') or build(node,root))['argv']
    if not argv[0]: raise ValueError(f'{lang} runtime unavailable')
    timeout=min(max(int(node.get('config',{}).get('timeout',300)),1),3600)
    started=time.monotonic()
    with (directory/'stdout.txt').open('w') as stdout, (directory/'stderr.txt').open('w') as stderr:
        proc=subprocess.Popen(argv,cwd=project_dir,env=env,stdout=stdout,stderr=stderr,start_new_session=True,**subprocess_lock_options())
        reason=None
        try:
            if callable(node.get('_on_process')):node['_on_process'](proc.pid)
            while proc.poll() is None:
                if cancelled():reason='cancelled'
                elif time.monotonic()-started>timeout:reason='timed_out'
                elif sum(p.stat().st_size for p in directory.rglob('*') if p.is_file())>110*1024*1024:reason='output quota exceeded'
                if reason:
                    try:os.killpg(proc.pid,signal.SIGTERM)
                    except ProcessLookupError:pass
                    try:proc.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        try:os.killpg(proc.pid,signal.SIGKILL)
                        except ProcessLookupError:pass
                        proc.wait()
                    break
                time.sleep(.05)
        finally:
            if proc.poll() is None:
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                proc.wait()
            if callable(node.get('_on_process_exit')):node['_on_process_exit']()
    logs={name:(directory/(name+'.txt')).read_text(errors='replace')[-100000:] for name in ('stdout','stderr')}
    if reason or proc.returncode: return {**logs,'status':reason if reason in {'cancelled','timed_out'} else 'failed','error':reason or f'Process exited {proc.returncode}'}
    try:
        if not out.exists():
            if node.get('outputs',{}).get('required'): raise ValueError('Missing required output file')
            output={'schemaVersion':1,'data':{},'artifacts':[]}
        else:
            if out.stat().st_size>100*1024*1024: raise ValueError('Structured output exceeds 100 MiB worker quota')
            output=json.loads(out.read_text(),parse_constant=lambda x:(_ for _ in ()).throw(ValueError('Non-finite JSON')))
        if not isinstance(output,dict) or output.get('schemaVersion')!=1 or not isinstance(output.get('data'),dict) or not isinstance(output.get('artifacts'),list): raise ValueError('Output must be a version 1 envelope with data object and artifacts array')
        portable(output['data'])
        check_schema(output['data'],node.get('outputs'))
        files=[];total=0
        for item in output.get('artifacts',[]):
            path=(artifacts/item.get('path',item.get('name',''))).resolve()
            if not path.is_relative_to(artifacts.resolve()) or not path.is_file() or path.is_symlink(): raise ValueError('Artifact must be a node-local file')
            total+=path.stat().st_size
            if total>100*1024*1024 or len(files)>=100: raise ValueError('Artifact quota exceeded')
            files.append({'name':item.get('name',path.name),'path':str(path),'size':path.stat().st_size,'mediaType':item.get('mediaType','application/octet-stream'),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        output['artifacts']=files
    except Exception as exc:
        raise WorkerOutputError(str(exc),logs) from exc
    return {**logs,'status':'succeeded','output':output}
