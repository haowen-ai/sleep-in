"""Remaining exact public API acceptance edges; isolated files and native tools."""
import copy
import json
import os
import shutil
from pathlib import Path

import pytest
from test_workflows import service, simple_graph, native_node
from test_workflow_api_contract import client, admin, saved
from test_acceptance_schedule_security import utc, execute, rows


def test_sch007_saved_daily_times_are_sorted_and_deduplicated(client):
    admin(client);wf=saved(client);prefix='/api/workflows/'+wf['id']
    wf['schedule']={'kind':'daily','times':['18:30','07:00','07:00']}
    response=client.put(prefix,json=wf);assert response.status_code==200
    assert response.json()['schedule']['times']==['07:00','18:30']
    assert client.get(prefix).json()['schedule']['times']==['07:00','18:30']
    preview=client.post(prefix+'/preview',json={'after':'2026-09-20T22:00Z'}).json()['next_runs']
    assert [utc(v) for v in preview[:2]]==[utc('2026-09-21T07:00Z'),utc('2026-09-21T18:30Z')]


def test_sch025_authenticated_pause_resume_skips_past_and_keeps_future(client):
    admin(client);wf=saved(client);prefix='/api/workflows/'+wf['id'];client.post(prefix+'/publish')
    from taskconsole.workflows import WorkflowService
    svc=WorkflowService(client.app.state.store)
    wf['triggers']=[{'id':'clock','kind':'scheduled','enabled':True,'schedule':{'kind':'daily','time':'07:00'},'timezone':'UTC'}]
    assert client.put(prefix,json=wf).status_code==200
    svc.tick(utc('2026-09-21T06:59:59Z'))
    wf['triggers'][0]['enabled']=False;assert client.put(prefix,json=wf).status_code==200
    assert svc.tick(utc('2026-09-21T07:00Z'))==[]
    wf['triggers'][0]['enabled']=True;assert client.put(prefix,json=wf).status_code==200
    assert svc.tick(utc('2026-09-21T08:00Z'))==[]
    preview=client.post(prefix+'/preview',json={'schedule':wf['triggers'][0]['schedule'],'after':'2026-09-21T08:00Z'}).json()['next_runs']
    assert utc(preview[0])==utc('2026-09-22T07:00Z')
    svc.tick(utc('2026-09-22T06:59:59Z'));next_day=svc.tick(utc('2026-09-22T07:00Z'))
    assert len(next_day)==1 and len(client.get('/api/workflow-runs').json())==1
    execute(svc,next_day[0])


def test_sch032_unavailable_engine_never_turns_accepted_202_into_success(client,monkeypatch,tmp_path):
    admin(client);wf=saved(client);prefix='/api/workflows/'+wf['id'];client.post(prefix+'/publish')
    from taskconsole.workflows import WorkflowService
    from taskconsole.workflows_n8n import execute_graph
    svc=WorkflowService(client.app.state.store)
    monkeypatch.setenv('SLEEP_IN_N8N_COMMAND',json.dumps([str(tmp_path/'not-installed-n8n')]))
    admitted=client.post(prefix+'/run',json={});assert admitted.status_code==202
    assert admitted.json()['status']=='queued'
    execute_graph(svc,admitted.json()['id'])
    result=client.get('/api/workflow-runs/'+admitted.json()['id']).json()
    assert result['status']=='failed' and 'n8n' in result['error'].lower()
    assert all(n['attempts']==[] for n in result['nodes'].values())
    wf['enabled']=True;wf['schedule']={'kind':'daily','time':'07:00'}
    assert client.put(prefix,json=wf).status_code==200
    svc.tick(utc('2026-09-21T06:59:59Z'));due=svc.tick(utc('2026-09-21T07:00Z'));assert len(due)==1
    execute_graph(svc,due[0]['id']);scheduled=client.get('/api/workflow-runs/'+due[0]['id']).json()
    assert scheduled['status']=='failed' and 'n8n' in scheduled['error'].lower()
    assert all(n['attempts']==[] for n in scheduled['nodes'].values())


@pytest.mark.parametrize('edges',[[{'source':'a','target':'a'}],[{'source':'a','target':'b'},{'source':'b','target':'a'}]])
def test_pub006_http_cannot_publish_self_loop_or_cycle(client,edges):
    admin(client);graph=simple_graph();graph['edges']=edges
    response=client.post('/api/workflows',json=graph);assert response.status_code==200
    wf=response.json();assert wf['validation_errors']
    rejected=client.post('/api/workflows/'+wf['id']+'/publish')
    assert rejected.status_code in {400,422}
    detail=rejected.json()['detail'];assert 'cycle' in detail['message'].lower() and detail['node_id'] in {'a','b'}
    assert client.get('/api/workflows/'+wf['id']).json()['published_version_id'] is None
    assert client.get('/api/workflow-runs').json()==[]


def test_pub007_actual_missing_java_compiler_is_unavailable_and_blocks_publish(client,monkeypatch,tmp_path):
    admin(client);empty=tmp_path/'no-tools';empty.mkdir();monkeypatch.setenv('PATH',str(empty))
    assert shutil.which('javac') is None
    profile=next(r for r in client.get('/api/runtimes').json() if r['language']=='java')
    assert profile['status']=='unavailable' and 'java' in profile['reason']
    wf=client.post('/api/workflows',json={'name':'Unavailable compiler','nodes':[{'id':'java','kind':'java','source':'public class Main {}','inputs':{},'config':{}}],'edges':[]}).json()
    rejected=client.post('/api/workflows/'+wf['id']+'/publish')
    assert rejected.status_code in {400,422} and rejected.json()['detail']['node_id']=='java'
    assert 'java' in rejected.text.lower() and client.get('/api/workflow-runs').json()==[]


@pytest.mark.parametrize('language',['c','java'])
def test_pub008_actual_compiler_errors_preserve_old_publication(client,language):
    admin(client)
    from taskconsole.workflows_runtime import runtimes
    profile=next(r for r in runtimes() if r['language']==language)
    if profile['status']!='ready':pytest.skip('BLOCKED_ENV: actual '+language+' compiler required')
    envelope=json.dumps({'schemaVersion':1,'data':{'value':7},'artifacts':[]},separators=(',',':'))
    literal=json.dumps(envelope)
    sources={
        'c':'#include <stdio.h>\n#include <stdlib.h>\nint main(void){FILE *f=fopen(getenv("SLEEP_IN_OUTPUT_FILE"),"w");if(!f)return 1;fputs('+literal+',f);fclose(f);return 0;}',
        'java':'import java.nio.file.*; public class Main { public static void main(String[] args) throws Exception {Files.writeString(Path.of(System.getenv("SLEEP_IN_OUTPUT_FILE")),'+literal+');}}'}
    graph={'name':'Compiler preservation','nodes':[{'id':'native','kind':language,'source':sources[language],'inputs':{},'config':{}}],'edges':[]}
    saved=client.post('/api/workflows',json=graph);assert saved.status_code==200;wf=saved.json();prefix='/api/workflows/'+wf['id']
    publication=client.post(prefix+'/publish');assert publication.status_code==200,publication.text;v1=publication.json()['version_id']
    wf['nodes'][0]['source']=('int main( { syntax error }' if language=='c' else 'public class Main { public static void main( { syntax error }')
    assert client.put(prefix,json=wf).status_code==200
    rejected=client.post(prefix+'/publish');assert rejected.status_code in {400,422}
    assert 'build failed' in rejected.text.lower() and 'error' in rejected.text.lower()
    assert rejected.json()['detail']['node_id']=='native'
    assert client.get(prefix).json()['published_version_id']==v1
    admitted=client.post(prefix+'/run',json={});assert admitted.status_code==202 and admitted.json()['version_id']==v1
    from taskconsole.workflows import WorkflowService
    svc=WorkflowService(client.app.state.store);run=execute(svc,svc.get_run(admitted.json()['id']))
    assert run['nodes']['native']['output']['data']=={'value':7}


def test_sec002_operator_can_execute_and_read_only_authorized_publication(client):
    admin(client);wf=saved(client);prefix='/api/workflows/'+wf['id'];client.post(prefix+'/publish')
    user=client.post('/api/admin/users',json={'username':'operator','password':'operator actual result password','role':'operator'})
    assert user.status_code==200;operator=user.json()
    denied=copy.deepcopy(wf);denied.pop('id');denied['name']='Not authorized';denied['allowed_user_ids']=['somebody-else']
    denied=client.post('/api/workflows',json=denied).json();client.post('/api/workflows/'+denied['id']+'/publish')
    client.post('/api/logout');login=client.post('/api/login',json={'username':'operator','password':'operator actual result password'});client.headers['X-CSRF-Token']=login.json()['csrf']
    run=client.post(prefix+'/run',json={});assert run.status_code==202
    from taskconsole.workflows import WorkflowService
    svc=WorkflowService(client.app.state.store);execute(svc,svc.get_run(run.json()['id']))
    output=client.get('/api/workflow-runs/'+run.json()['id']+'/nodes/source/output');assert output.status_code==200 and output.json()['data']=={'value':7}
    assert client.get('/api/workflows/'+denied['id']).status_code==403
    assert client.post('/api/workflows/'+denied['id']+'/run',json={}).status_code==403
    assert all(w['id']!=denied['id'] for w in client.get('/api/workflows').json())


def test_ops001_portable_export_import_into_fresh_workspace_requires_rebinding(tmp_path):
    from taskconsole.workflows_transfer import export_template,import_template
    from taskconsole.workflows_sql import create_connection
    from test_workflow_transfer import authored_graph
    source=service(tmp_path/'source');target=service(tmp_path/'fresh-target')
    connection=create_connection(source.store,{'name':'Private synthetic','dialect':'postgresql','config':{'host':'private.invalid','password':'synthetic-private-export-token'}})
    graph=authored_graph();graph['nodes'][0]['config']['connection_id']=connection['id'];original=source.save(graph)
    with source.store.transaction() as tx:
        wf=tx.get('workflow',original['id']);wf['triggers']=[{'id':'old','enabled':True}];tx.put('workflow',wf)
        tx.put('workflow_run',{'id':'private-history','workflow_id':wf['id'],'status':'succeeded'})
    template=export_template(source.store,original['id']);encoded=json.dumps(template)
    assert all(secret not in encoded for secret in ['synthetic-private-export-token','private.invalid','private-history',connection['id']])
    assert rows(target,'workflow')==[] and rows(target,'connection')==[]
    imported=import_template(target.store,template)
    assert imported['enabled'] is False and imported.get('triggers',[])==[] and imported['published_version_id'] is None
    assert imported['edges']==original['edges'] and imported['params']==original['params']
    for old,new in zip(original['nodes'],imported['nodes']):
        assert {k:new[k] for k in ['id','name','kind','source','inputs','outputs','position']}=={k:old[k] for k in ['id','name','kind','source','inputs','outputs','position']}
    assert imported['requires_rebinding'] and any('connection' in e['message'].lower() for e in imported['validation_errors'])
    assert rows(target,'connection')==[] and rows(target,'workflow_run')==[]
