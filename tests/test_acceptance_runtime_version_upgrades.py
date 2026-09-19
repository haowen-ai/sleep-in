"""Authenticated native runtime/dependency upgrades and publication build identity."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

import pytest
from test_acceptance_data import live, configured_node
from test_acceptance_branches import calls, requires_n8n
from test_acceptance_gold_concurrency import publish, trace
from taskconsole.workflows_n8n import execute_graph
from taskconsole.workflows_runtime import run_script, verify_frozen_node


def post(client,path,body):
    response=client.post(path,json=body);assert response.status_code==200,response.text
    return response.json()


def build_pack(client,profile,version=None):
    version=post(client,'/api/runtime-profiles/'+profile['id']+'/build',{'version_id':version} if version else {})
    assert version['status']=='ready' and version['verification']['status']=='passed',version.get('build_log')
    return version


def wheel(directory,version,value):
    path=directory/f'acceptance_dependency-{version}-py3-none-any.whl'
    with zipfile.ZipFile(path,'w') as archive:
        archive.writestr('acceptance_dependency.py','VALUE='+repr(value)+'\n')
        prefix=f'acceptance_dependency-{version}.dist-info/'
        archive.writestr(prefix+'METADATA',f'Metadata-Version: 2.1\nName: acceptance-dependency\nVersion: {version}\n')
        archive.writestr(prefix+'WHEEL','Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n')
        archive.writestr(prefix+'RECORD','')
    return f'acceptance-dependency=={version} --hash=sha256:'+hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.mark.parametrize('field',['executable','java'])
@pytest.mark.parametrize('unavailable',['missing','nonexecutable'])
def test_pinned_runtime_missing_or_nonexecutable_tool_is_actionable(tmp_path,field,unavailable):
    tool=tmp_path/'owned-tool';tool.write_text('#!/bin/sh\nexit 0\n');tool.chmod(0o700)
    digest=hashlib.sha256(tool.read_bytes()).hexdigest()
    node={'_runtime':{field:str(tool),field+'_sha256':digest,'directory':str(tmp_path),'manifest':{'owned-tool':digest}}}
    if unavailable=='missing':tool.unlink()
    else:tool.chmod(0o600)
    with pytest.raises(ValueError,match='(?i)runtime.*unavailable'):
        verify_frozen_node(node)


@requires_n8n
def test_authenticated_managed_dependency_upgrade_pins_old_run_and_blocks_missing_old_executable(live):
    client=live.acceptance_client;wheelhouse=live.store.path/'wheelhouse';wheelhouse.mkdir()
    config={'wheelhouse':str(wheelhouse),'requirements_lock':wheel(wheelhouse,'1.0','dependency-v1'),'shared_files':{'shared.py':'VALUE="shared-v1"\n'}}
    profile=post(client,'/api/runtime-profiles',{'name':'Pinned Python upgrades','language':'python','config':config})
    v1=build_pack(client,profile)
    assert 'acceptance-dependency==1.0' in v1['dependency_summary']
    source='from pathlib import Path\nimport sys,shared,acceptance_dependency\ndef main(inputs):\n with Path("business-calls").open("a") as f:f.write("call\\n")\n return {"shared":shared.VALUE,"dependency":acceptance_dependency.VALUE,"executable":sys.executable}'
    graph={'name':'Immutable runtime upgrades','nodes':[{'id':'worker','kind':'python','source':source,'inputs':{},'config':{'runtime_version_id':v1['id']}}],'edges':[]}
    wf,pub1=publish(live,graph);wid=wf['id']
    old=client.post('/api/workflows/'+wid+'/run',json={});assert old.status_code==202
    old_id=old.json()['id'];old_snapshot=copy.deepcopy(live.get_run(old_id)['snapshot'])
    config2={**config,'requirements_lock':wheel(wheelhouse,'2.0','dependency-v2'),'shared_files':{'shared.py':'VALUE="shared-v2"\n'}}
    draft2=post(client,'/api/runtime-profiles/'+profile['id']+'/versions',{'config':config2});v2=build_pack(client,profile,draft2['id'])
    assert 'acceptance-dependency==2.0' in v2['dependency_summary']
    assert v2['id']!=v1['id'] and v2['digest']!=v1['digest']
    graph['nodes'][0]['config']['runtime_version_id']=v2['id']
    update=client.put('/api/workflows/'+wid,json=graph);assert update.status_code==200,update.text
    pub2=post(client,'/api/workflows/'+wid+'/publish',{})
    execute_graph(live,old_id);old=live.get_run(old_id)
    assert old['status']=='succeeded' and old['version_id']==pub1['version_id'] and old['snapshot']==old_snapshot
    old_runtime=old_snapshot['nodes'][0]['_runtime'];old_executable=Path(old_runtime['executable'])
    assert old['nodes']['worker']['output']['data']=={'shared':'shared-v1','dependency':'dependency-v1','executable':str(old_executable)}
    calls(live,old,'worker',1);assert 'node_worker' in trace(live,old)
    response=client.post('/api/workflows/'+wid+'/run',json={});assert response.status_code==202
    new_id=response.json()['id'];execute_graph(live,new_id);new=live.get_run(new_id)
    new_runtime=new['snapshot']['nodes'][0]['_runtime']
    assert new['status']=='succeeded' and new['version_id']==pub2['version_id']
    assert new['nodes']['worker']['output']['data']=={'shared':'shared-v2','dependency':'dependency-v2','executable':new_runtime['executable']}
    assert new_runtime['id']==v2['id'] and new_runtime['digest']==v2['digest'] and new_runtime['executable']!=str(old_executable)
    calls(live,new,'worker',1);assert 'node_worker' in trace(live,new)
    response=client.post('/api/workflows/'+wid+'/run',json={'version_id':pub1['version_id']});assert response.status_code==202
    missing_id=response.json()['id'];held=live.store.path/'held-owned-old-python'
    assert old_executable.is_relative_to(live.store.path/'runtime-packs') and not old_executable.is_symlink()
    old_executable.rename(held)
    try:
        execute_graph(live,missing_id);missing=live.get_run(missing_id);state=missing['nodes']['worker']
        assert missing['status']=='failed' and missing['version_id']==pub1['version_id']
        assert missing['snapshot']==old_snapshot and state.get('output') is None and not state.get('worker_started')
        assert state['retryable'] is False and len(state['attempts'])==1
        assert not (live.store.path/'workflow-runs'/missing_id/'worker'/'attempt-1'/'project'/'business-calls').exists()
        assert 'runtime' in state['error'].lower() and any(word in state['error'].lower() for word in ['missing','unavailable']),state['error']
        assert missing.get('graph_execution_id') and missing.get('n8n_execution_id')
    finally:held.rename(old_executable)
    assert live.get_run(old_id)==old and live.get_run(new_id)==new


C_SOURCE='''#include <stdio.h>
#include <stdlib.h>
#include "dependency.h"
#ifndef FLAG
#define FLAG 0
#endif
#ifdef __clang__
#define FAMILY "clang"
#else
#define FAMILY "gcc"
#endif
int main(void){FILE*f=fopen(getenv("SLEEP_IN_OUTPUT_FILE"),"w");if(!f)return 1;fprintf(f,"{\\"schemaVersion\\":1,\\"data\\":{\\"family\\":\\"%s\\",\\"flag\\":%d,\\"dependency\\":%d},\\"artifacts\\":[]}",FAMILY,FLAG,DEPENDENCY);return fclose(f);}
'''


def source_project(client,value):
    return post(client,'/api/source-projects',{'name':'Native dependency','language':'c','entrypoint':'main.c','files':{'main.c':C_SOURCE,'dependency.h':f'#define DEPENDENCY {value}\n'}})


def published_node(svc,wid):
    publication=post(svc.acceptance_client,'/api/workflows/'+wid+'/publish',{})
    with svc.store.transaction() as tx:version=tx.get('workflow_version',publication['version_id'])
    return version['snapshot']['nodes'][0]


def native_result(svc,node,label):
    result=run_script(node,{},svc.store.path/label,svc.store.path)
    assert result['status']=='succeeded',result
    return result['output']['data']


def test_authenticated_publication_cache_tracks_real_compile_flags_and_header_dependency(live):
    client=live.acceptance_client;compiler=shutil.which('clang') or shutil.which('gcc')
    if not compiler:pytest.skip('A real native C compiler is required')
    family='clang' if 'clang' in subprocess.run([compiler,'--version'],capture_output=True,text=True,check=True).stdout.lower() else 'gcc'
    profile=post(client,'/api/runtime-profiles',{'language':'c','config':{'executable':compiler}});version=build_pack(client,profile)
    project1=source_project(client,7);project2=source_project(client,11)
    config={'runtime_version_id':version['id'],'project_id':project1['id'],'compile_args':['-DFLAG=3']}
    graph={'name':'Native cache flags and dependencies','nodes':[{'id':'c','kind':'c','config':config,'inputs':{}}],'edges':[]}
    wf=post(client,'/api/workflows',graph);first=published_node(live,wf['id'])
    assert native_result(live,first,'worker-first')=={'family':family,'flag':3,'dependency':7}
    target=Path(first['_build']['argv'][0]);before=(target.stat().st_mtime_ns,hashlib.sha256(target.read_bytes()).hexdigest())
    cached=published_node(live,wf['id'])
    assert cached['_build']['digest']==first['_build']['digest'] and (target.stat().st_mtime_ns,hashlib.sha256(target.read_bytes()).hexdigest())==before
    config['compile_args']=['-DFLAG=9'];response=client.put('/api/workflows/'+wf['id'],json=graph);assert response.status_code==200,response.text
    flags=published_node(live,wf['id'])
    assert flags['_runtime']['id']==first['_runtime']['id'] and flags['_project']['digest']==first['_project']['digest']
    assert flags['_build']['digest']!=first['_build']['digest']
    assert native_result(live,flags,'worker-flags')=={'family':family,'flag':9,'dependency':7}
    config['project_id']=project2['id'];response=client.put('/api/workflows/'+wf['id'],json=graph);assert response.status_code==200,response.text
    dependency=published_node(live,wf['id'])
    assert dependency['_runtime']['id']==flags['_runtime']['id'] and dependency['_project']['digest']!=flags['_project']['digest']
    assert dependency['_build']['digest']!=flags['_build']['digest']
    assert native_result(live,dependency,'worker-dependency')=={'family':family,'flag':9,'dependency':11}
    assert native_result(live,first,'worker-original-again')=={'family':family,'flag':3,'dependency':7}
    assert (target.stat().st_mtime_ns,hashlib.sha256(target.read_bytes()).hexdigest())==before


def test_authenticated_same_path_real_gcc_to_clang_upgrade_invalidates_native_cache(live):
    client=live.acceptance_client
    gcc=os.environ.get('SLEEP_IN_TEST_GCC') or shutil.which('gcc');clang=os.environ.get('SLEEP_IN_TEST_CLANG') or shutil.which('clang')
    if not gcc or not clang:pytest.skip('Distinct actual GCC and Clang toolchains required')
    versions=[subprocess.run([tool,'--version'],capture_output=True,text=True,check=True).stdout for tool in [gcc,clang]]
    digests=[hashlib.sha256(Path(tool).read_bytes()).hexdigest() for tool in [gcc,clang]]
    if 'clang' in versions[0].lower() or 'clang' not in versions[1].lower() or digests[0]==digests[1]:pytest.skip('System gcc aliases Clang; distinct real GCC/Clang upgrade available in Linux CI')
    selected=live.store.path/'selected-compiler';selected.symlink_to(Path(gcc).resolve())
    config={'executable':str(selected)};profile=post(client,'/api/runtime-profiles',{'language':'c','config':config});v1=build_pack(client,profile)
    project=source_project(client,7);graph={'name':'Real same-path compiler upgrade','nodes':[{'id':'c','kind':'c','inputs':{},'config':{'runtime_version_id':v1['id'],'project_id':project['id']}}],'edges':[]}
    wf=post(client,'/api/workflows',graph);first=published_node(live,wf['id'])
    assert native_result(live,first,'gcc-worker')=={'family':'gcc','flag':0,'dependency':7}
    selected.unlink();selected.symlink_to(Path(clang).resolve())
    draft=post(client,'/api/runtime-profiles/'+profile['id']+'/versions',{'config':config});v2=build_pack(client,profile,draft['id'])
    graph['nodes'][0]['config']['runtime_version_id']=v2['id'];response=client.put('/api/workflows/'+wf['id'],json=graph);assert response.status_code==200,response.text
    second=published_node(live,wf['id'])
    assert first['_runtime']['compiler']==second['_runtime']['compiler']==str(selected)
    assert first['_runtime']['tool_sha256']==digests[0] and second['_runtime']['tool_sha256']==digests[1]
    assert first['_runtime']['tool_version']!=second['_runtime']['tool_version'] and first['_runtime']['digest']!=second['_runtime']['digest']
    assert first['_project']['digest']==second['_project']['digest'] and first['_build']['digest']!=second['_build']['digest']
    assert native_result(live,second,'clang-worker')=={'family':'clang','flag':0,'dependency':7}
