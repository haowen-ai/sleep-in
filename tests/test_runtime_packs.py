import io
import json
import os
import shutil
import zipfile
from pathlib import Path
import pytest
from taskconsole.store import Store

def available_node():
    path=os.environ.get('SLEEP_IN_NODE') or shutil.which('node')
    if not path or not Path(path).is_file():pytest.skip('A working Node executable is required')
    return path


@pytest.fixture
def store(tmp_path):
    value=Store(tmp_path,'sqlite:///'+str(tmp_path/'state.sqlite'))
    yield value
    value.engine.dispose()


def test_python_pack_versions_shared_modules_and_frozen_execution(store,tmp_path):
    from taskconsole.workflows_packs import RuntimePacks,prepare_node
    from taskconsole.workflows_runtime import run_script
    packs=RuntimePacks(store)
    profile=packs.create({'name':'Python shared','language':'python','config':{'shared_files':{'shared.py':'VALUE = 7\n'}}})
    v1=packs.build(profile['id'])
    assert v1['status']=='ready' and v1['verification']['status']=='passed'
    node=prepare_node(store,{'id':'n','kind':'python','source':'import shared\ndef main(inputs): return {"v":shared.VALUE,"nil":inputs["nil"]}','inputs':{},'config':{'runtime_version_id':v1['id']}})
    second=packs.new_version(profile['id'],{'shared_files':{'shared.py':'VALUE = 9\n'}})
    v2=packs.build(profile['id'],second['id'])
    assert v1['id']!=v2['id'] and v1['digest']!=v2['digest']
    result=run_script(node,{'nil':None},tmp_path/'execution',store.path)
    assert result['output']['data']=={'v':7,'nil':None}
    with pytest.raises(ValueError,match='immutable'):packs.build(profile['id'],v1['id'])


def test_project_zip_rejects_traversal_and_symlinks_before_write(store):
    from taskconsole.workflows_projects import SourceProjects
    projects=SourceProjects(store)
    for name,symlink in [('../outside.py',False),('/outside.py',False),('link.py',True)]:
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            info=zipfile.ZipInfo(name)
            if symlink:info.external_attr=(0o120777<<16)
            archive.writestr(info,'outside')
        with pytest.raises(ValueError):projects.upload('bad','python','main.py',stream.getvalue(),'code.zip')
    assert projects.list()==[]


def test_source_project_entrypoint_and_digest(store):
    from taskconsole.workflows_projects import SourceProjects
    projects=SourceProjects(store)
    data={'name':'Project','language':'python','entrypoint':'pkg/main.py','files':{'pkg/main.py':'def main(inputs): return inputs','pkg/util.py':'VALUE=1'}}
    project=projects.create(data)
    assert project['digest'] and project['files']==['pkg/main.py','pkg/util.py']
    with pytest.raises(ValueError,match='entrypoint'):projects.create({**data,'entrypoint':'missing.py'})
    changed=projects.create({**data,'files':{**data['files'],'pkg/util.py':'VALUE=2'}})
    assert changed['digest']!=project['digest']


def test_hashless_python_dependency_fails_pack_build(store):
    from taskconsole.workflows_packs import RuntimePacks
    packs=RuntimePacks(store)
    profile=packs.create({'name':'Bad lock','language':'python','config':{'requirements_lock':'packaging==24.2'}})
    version=packs.build(profile['id'])
    assert version['status']=='failed' and 'hash' in version['build_log'].lower()


def test_esm_project_executes_with_pinned_node_pack(store,tmp_path,monkeypatch):
    from taskconsole.workflows_packs import RuntimePacks,prepare_node
    from taskconsole.workflows_projects import SourceProjects
    from taskconsole.workflows_runtime import run_script
    node_path=available_node()
    if not Path(node_path).exists():pytest.skip('Configured Node unavailable')
    monkeypatch.setenv('SLEEP_IN_NODE',node_path)
    profile=RuntimePacks(store).create({'name':'ESM','language':'javascript','config':{'module_format':'esm','shared_files':{'helper.mjs':'export const value = 7;'}}})
    version=RuntimePacks(store).build(profile['id']);assert version['status']=='ready',version
    project=SourceProjects(store).create({'name':'ESM project','language':'javascript','entrypoint':'main.mjs','files':{'main.mjs':'import {value} from "./helper.mjs"; export async function main(inputs){return {value,received:inputs};}'}})
    node=prepare_node(store,{'id':'esm','kind':'javascript','source':'','config':{'runtime_version_id':version['id'],'project_id':project['id']}})
    result=run_script(node,{'text':'中文','n':None},tmp_path/'run',store.path)
    assert result['output']['data']=={'value':7,'received':{'text':'中文','n':None}}


def test_ready_runtime_rejects_changed_shared_module(store):
    from taskconsole.workflows_packs import RuntimePacks,prepare_node
    packs=RuntimePacks(store);p=packs.create({'language':'shell','config':{'shared_files':{'value.txt':'one'}}});v=packs.build(p['id'])
    assert v['status']=='ready'
    private=packs.version(v['id'],True);(Path(private['directory'])/'shared/value.txt').write_text('two')
    with pytest.raises(ValueError,match='Immutable runtime'):prepare_node(store,{'id':'x','kind':'shell','source':'true','config':{'runtime_version_id':v['id']}})


def test_toolchain_rejects_corrupt_download(store,tmp_path):
    from taskconsole.workflows_toolchains import Toolchains
    archive=tmp_path/'bad.tar.gz';archive.write_bytes(b'not an archive')
    record=Toolchains(store).install_manifest({'id':'test','name':'Fixture','language':'java','url':'https://example.invalid/file.tar.gz','sha256':'0'*64,'format':'tar','executable':'bin/java'},archive)
    assert record['status']=='failed' and 'checksum' in record['error'].lower()
    assert not (store.path/'toolchains/test/home').exists()


def test_runtime_routes_admin_only_and_structured_errors(store):
    from fastapi import FastAPI,HTTPException
    from fastapi.testclient import TestClient
    from taskconsole.workflows_packs import register_runtime_routes
    app=FastAPI()
    def require(request,tx,admin=False):
        if request.headers.get('x-role')!='admin':raise HTTPException(403,detail={'code':'forbidden','message':'Admin required'})
        return {'id':'admin'}
    register_runtime_routes(app,store,require)
    c=TestClient(app)
    assert c.get('/api/runtime-profiles').status_code==403
    response=c.post('/api/runtime-profiles',headers={'x-role':'admin'},json={'name':'Shell','language':'shell'})
    assert response.status_code==200,response.text
    p=response.json();assert p['versions'][0]['status']=='draft'
    assert c.post('/api/runtime-profiles/'+p['id']+'/build',headers={'x-role':'admin'},json={}).json()['status']=='ready'
    bad=c.post('/api/source-projects',headers={'x-role':'admin'},json={'files':{'../x':'bad'},'entrypoint':'../x','language':'python'})
    assert bad.status_code==400 and isinstance(bad.json()['detail']['message'],str)


def test_nested_esm_entrypoint_may_import_fs(store,tmp_path,monkeypatch):
    from taskconsole.workflows_packs import RuntimePacks,prepare_node
    from taskconsole.workflows_projects import SourceProjects
    from taskconsole.workflows_runtime import run_script
    monkeypatch.setenv('SLEEP_IN_NODE',available_node())
    packs=RuntimePacks(store);p=packs.create({'language':'javascript','config':{'module_format':'esm'}});v=packs.build(p['id'])
    project=SourceProjects(store).create({'language':'javascript','entrypoint':'src/main.mjs','files':{'src/main.mjs':'import fs from "node:fs"; import {v} from "./helper.mjs"; export function main(inputs){ return {v,exists:fs.existsSync(process.env.SLEEP_IN_INPUT_FILE)}; }','src/helper.mjs':'export const v = 9;'}})
    node=prepare_node(store,{'id':'x','kind':'javascript','config':{'project_id':project['id'],'runtime_version_id':v['id']}})
    result=run_script(node,{},tmp_path/'run',store.path)
    assert result['status']=='succeeded',result
    assert result['output']['data']=={'v':9,'exists':True}


def test_python_package_relative_import(store,tmp_path):
    from taskconsole.workflows_packs import RuntimePacks,prepare_node
    from taskconsole.workflows_projects import SourceProjects
    from taskconsole.workflows_runtime import run_script
    packs=RuntimePacks(store);p=packs.create({'language':'python'});v=packs.build(p['id'])
    project=SourceProjects(store).create({'language':'python','entrypoint':'pkg/main.py','files':{'pkg/__init__.py':'','pkg/main.py':'from .util import VALUE\ndef main(inputs): return {"value": VALUE}','pkg/util.py':'VALUE=11'}})
    node=prepare_node(store,{'id':'x','kind':'python','config':{'project_id':project['id'],'runtime_version_id':v['id']}})
    result=run_script(node,{},tmp_path/'run',store.path)
    assert result['status']=='succeeded',result
    assert result['output']['data']=={'value':11}


def test_real_hash_locked_python_wheel_and_bad_hash(store,tmp_path):
    import hashlib
    from taskconsole.workflows_packs import RuntimePacks,prepare_node
    from taskconsole.workflows_runtime import run_script
    wheel=tmp_path/'sleepin_fixture-1.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel,'w') as z:
        z.writestr('sleepin_fixture.py','VALUE=42\n')
        z.writestr('sleepin_fixture-1.0.dist-info/METADATA','Metadata-Version: 2.1\nName: sleepin-fixture\nVersion: 1.0\n')
        z.writestr('sleepin_fixture-1.0.dist-info/WHEEL','Wheel-Version: 1.0\nGenerator: sleepin-test\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        z.writestr('sleepin_fixture-1.0.dist-info/RECORD','')
    sha=hashlib.sha256(wheel.read_bytes()).hexdigest();packs=RuntimePacks(store)
    config={'wheelhouse':str(tmp_path),'requirements_lock':'sleepin-fixture==1.0 --hash=sha256:'+sha}
    profile=packs.create({'language':'python','config':config});v=packs.build(profile['id']);assert v['status']=='ready',v
    node=prepare_node(store,{'id':'wheel','kind':'python','source':'import sleepin_fixture\ndef main(inputs): return {"value":sleepin_fixture.VALUE}','config':{'runtime_version_id':v['id']}})
    assert run_script(node,{},tmp_path/'run',store.path)['output']['data']=={'value':42}
    bad=packs.new_version(profile['id'],{**config,'requirements_lock':'sleepin-fixture==1.0 --hash=sha256:'+'0'*64})
    assert packs.build(profile['id'],bad['id'])['status']=='failed'


def test_real_npm_locked_dependency_cjs(store,tmp_path):
    import subprocess,tarfile
    from taskconsole.workflows_packs import RuntimePacks,prepare_node
    from taskconsole.workflows_runtime import run_script
    npm=os.environ.get('SLEEP_IN_TEST_NPM')
    if not npm:pytest.skip('Set SLEEP_IN_TEST_NPM to verified official npm executable')
    package=tmp_path/'fixture';package.mkdir();(package/'package.json').write_text(json.dumps({'name':'sleepin-fixture','version':'1.0.0','main':'index.js'}));(package/'index.js').write_text('module.exports={value:43};')
    archive=tmp_path/'fixture.tgz'
    with tarfile.open(archive,'w:gz') as tar:tar.add(package,arcname='package')
    config={'name':'fixture-consumer','version':'1.0.0','private':True,'dependencies':{'sleepin-fixture':'file:'+str(archive)}}
    lockdir=tmp_path/'lock';lockdir.mkdir();(lockdir/'package.json').write_text(json.dumps(config))
    env={**os.environ,'PATH':str(Path(npm).parent)+':'+os.environ.get('PATH','')}
    subprocess.run([npm,'install','--package-lock-only','--ignore-scripts','--no-audit','--no-fund'],cwd=lockdir,env=env,check=True,capture_output=True)
    lock=json.loads((lockdir/'package-lock.json').read_text())
    packs=RuntimePacks(store);p=packs.create({'language':'javascript','config':{'executable':str(Path(npm).parent/'node'),'npm_executable':npm,'package_json':config,'package_lock':lock}})
    v=packs.build(p['id']);assert v['status']=='ready',v
    archive.unlink()
    node=prepare_node(store,{'id':'npm','kind':'javascript','source':'const dep=require("sleepin-fixture");module.exports.main=async inputs=>({value:dep.value});','config':{'runtime_version_id':v['id']}})
    assert run_script(node,{},tmp_path/'run',store.path)['output']['data']=={'value':43}


def test_frozen_node_detects_pack_tamper_at_execution(store,tmp_path):
    from taskconsole.workflows_packs import RuntimePacks,prepare_node
    from taskconsole.workflows_runtime import run_script
    packs=RuntimePacks(store);p=packs.create({'language':'shell','config':{'shared_files':{'shared.txt':'original'}}});v=packs.build(p['id'])
    node=prepare_node(store,{'id':'shell','kind':'shell','source':'true','config':{'runtime_version_id':v['id']}})
    (Path(node['_runtime']['directory'])/'shared/shared.txt').write_text('changed')
    with pytest.raises(ValueError,match='Immutable runtime'):run_script(node,{},tmp_path/'run',store.path)


def test_python_lock_must_include_transitive_requirements(store,tmp_path):
    import hashlib
    from taskconsole.workflows_packs import RuntimePacks
    wheel=tmp_path/'sleepin_incomplete-1.0-py3-none-any.whl'
    with zipfile.ZipFile(wheel,'w') as z:
        z.writestr('sleepin_incomplete.py','VALUE=1')
        z.writestr('sleepin_incomplete-1.0.dist-info/METADATA','Metadata-Version: 2.1\nName: sleepin-incomplete\nVersion: 1.0\nRequires-Dist: missing-transitive>=1\n')
        z.writestr('sleepin_incomplete-1.0.dist-info/WHEEL','Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        z.writestr('sleepin_incomplete-1.0.dist-info/RECORD','')
    sha=hashlib.sha256(wheel.read_bytes()).hexdigest();packs=RuntimePacks(store)
    p=packs.create({'language':'python','config':{'wheelhouse':str(tmp_path),'requirements_lock':'sleepin-incomplete==1.0 --hash=sha256:'+sha}})
    v=packs.build(p['id']);assert v['status']=='failed',v
    assert 'missing-transitive' in v['build_log']


def test_npm_dev_dependency_also_requires_lock(store,monkeypatch):
    from taskconsole.workflows_packs import RuntimePacks
    monkeypatch.setenv('SLEEP_IN_NODE',available_node())
    packs=RuntimePacks(store);p=packs.create({'language':'javascript','config':{'package_json':{'name':'fixture','version':'1.0.0','devDependencies':{'typescript':'5.8.3'}}}})
    v=packs.build(p['id']);assert v['status']=='failed' and 'lock' in v['build_log'].lower()


def test_project_working_directory_contains_uploaded_resources(store,tmp_path):
    from taskconsole.workflows_packs import RuntimePacks,prepare_node
    from taskconsole.workflows_projects import SourceProjects
    from taskconsole.workflows_runtime import run_script
    packs=RuntimePacks(store);p=packs.create({'language':'python'});v=packs.build(p['id'])
    project=SourceProjects(store).create({'language':'python','entrypoint':'main.py','files':{'main.py':'from pathlib import Path\ndef main(inputs):return {"text":Path("resources/value.txt").read_text()}','resources/value.txt':'资源'}})
    node=prepare_node(store,{'id':'resource','kind':'python','config':{'runtime_version_id':v['id'],'project_id':project['id']}})
    result=run_script(node,{},tmp_path/'run',store.path)
    assert result['status']=='succeeded',result
    assert result['output']['data']=={'text':'资源'}


def test_javascript_nonfinite_output_is_not_silently_null(store,tmp_path,monkeypatch):
    from taskconsole.workflows_packs import RuntimePacks,prepare_node
    from taskconsole.workflows_runtime import run_script
    monkeypatch.setenv('SLEEP_IN_NODE',available_node())
    packs=RuntimePacks(store);p=packs.create({'language':'javascript'});v=packs.build(p['id'])
    node=prepare_node(store,{'id':'finite','kind':'javascript','source':'function main(inputs){return {n:NaN}}','config':{'runtime_version_id':v['id']}})
    result=run_script(node,{},tmp_path/'run',store.path)
    assert result['status']=='failed' and 'finite' in result['stderr'].lower()


def test_injected_symlink_changes_runtime_manifest(store,tmp_path):
    from taskconsole.workflows_packs import RuntimePacks,prepare_node
    packs=RuntimePacks(store);p=packs.create({'language':'shell'});v=packs.build(p['id']);private=packs.version(v['id'],True)
    (Path(private['directory'])/'shared/injected').symlink_to('/etc/hosts')
    with pytest.raises(ValueError,match='Immutable runtime'):prepare_node(store,{'id':'x','kind':'shell','source':'true','config':{'runtime_version_id':v['id']}})


def test_process_lifecycle_hooks_receive_live_pid_and_terminal_exit(store,tmp_path):
    import signal
    from taskconsole.workflows_runtime import run_script
    events=[]
    def started(pid):os.kill(pid,0);events.append(('start',pid))
    def exited():events.append(('exit',None))
    result=run_script({'id':'hooks','kind':'shell','source':'sleep 0.2','_on_process':started,'_on_process_exit':exited}, {},tmp_path/'run',store.path)
    assert result['status']=='succeeded'
    assert [event[0] for event in events]==['start','exit']
    with pytest.raises(ProcessLookupError):os.kill(events[0][1],0)


def test_process_is_cleaned_up_when_cancel_probe_raises(store,tmp_path):
    from taskconsole.workflows_runtime import run_script
    pids=[];events=[]
    def probe():raise RuntimeError('store unavailable')
    try:
        with pytest.raises(RuntimeError,match='store unavailable'):
            run_script({'id':'hooks','kind':'shell','source':'sleep 30','_on_process':pids.append,'_on_process_exit':lambda:events.append('exit')},{},tmp_path/'run',store.path,probe)
        assert events==['exit']
        with pytest.raises(ProcessLookupError):os.kill(pids[0],0)
    finally:
        if pids:
            try:os.killpg(pids[0],9)
            except ProcessLookupError:pass
