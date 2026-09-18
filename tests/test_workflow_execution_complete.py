import copy
import time
import pytest
from test_workflows import service, simple_graph


def test_node_only_explicit_inputs_and_frozen_draft(tmp_path):
    svc=service(tmp_path); graph=simple_graph({'v':{'source':'node','node_id':'a','path':'x'}})
    graph['nodes'][0]['source']='def main(inputs): raise RuntimeError("upstream forbidden")'
    wf=svc.save(graph)
    assert hasattr(svc,'test_node'), 'node-only admission missing'
    with pytest.raises(ValueError):svc.test_node(wf['id'],'b',{})
    run=svc.test_node(wf['id'],'b',{'inputs':{'v':42}})
    assert set(run['nodes'])=={'b'} and run['snapshot']['edges']==[] and run['test']
    svc.execute_node(run['id'],'b');result=svc.finish(run['id'])
    assert result['status']=='succeeded' and result['nodes']['b']['output']['data']=={'received':{'v':42}}


def test_async_submission_deduplicates_actual_process(tmp_path):
    svc=service(tmp_path);graph=simple_graph();graph['nodes']=graph['nodes'][:1];graph['edges']=[]
    graph['nodes'][0]['source']='import time\ndef main(inputs):\n time.sleep(.4)\n return {"x":7}'
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id'])
    assert hasattr(svc,'submit_node'),'async submit missing'
    start=time.monotonic();svc.submit_node(run['id'],'a');svc.submit_node(run['id'],'a')
    assert time.monotonic()-start<.3
    until=time.monotonic()+5
    while not svc.node_status(run['id'],'a')['terminal'] and time.monotonic()<until:time.sleep(.02)
    assert svc.node_status(run['id'],'a')['status']=='succeeded'
    assert len(svc.get_run(run['id'])['nodes']['a']['attempts'])==1


def test_retry_explicit_bounded_attempts(tmp_path):
    svc=service(tmp_path);graph=simple_graph();graph['nodes']=graph['nodes'][:1];graph['edges']=[]
    graph['nodes'][0]['config']['retry']={'max_attempts':2,'delay_seconds':0,'safe_to_retry':True}
    graph['nodes'][0]['source']='import os\ndef main(inputs):\n if "attempt-1" in os.environ["SLEEP_IN_INPUT_FILE"]: raise RuntimeError("transient")\n return {"ok":True}'
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id']);svc.execute_node(run['id'],'a')
    assert hasattr(svc,'node_status'),'retry status missing'
    assert svc.node_status(run['id'],'a')['retry_ready']
    svc.submit_node(run['id'],'a',retry=True)
    until=time.monotonic()+5
    while not svc.node_status(run['id'],'a')['terminal'] and time.monotonic()<until:time.sleep(.02)
    result=svc.finish(run['id']);assert result['status']=='succeeded'
    assert [a['status'] for a in result['nodes']['a']['attempts']]==['failed','succeeded']


def test_retry_without_safe_declaration_blocks_publication(tmp_path):
    svc=service(tmp_path);graph=simple_graph();graph['nodes'][0]['config']['retry']={'max_attempts':2}
    wf=svc.save(graph)
    with pytest.raises(ValueError,match='safe_to_retry'):svc.publish(wf['id'])


def test_artifact_crossnode_local_copy_and_checksum(tmp_path):
    svc=service(tmp_path);graph=simple_graph({'file':{'source':'artifact','node_id':'a','name':'hello.txt'}})
    graph['nodes'][0]['config']['entry_mode']='file'
    graph['nodes'][0]['source']='import os,json,pathlib\np=pathlib.Path(os.environ["SLEEP_IN_ARTIFACT_DIR"])/"hello.txt"\np.write_text("complete content")\njson.dump({"schemaVersion":1,"data":{},"artifacts":[{"name":"hello.txt"}]},open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))'
    graph['nodes'][1]['source']='from pathlib import Path\ndef main(inputs): return {"text":Path(inputs["file"]["path"]).read_text()}'
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id'])
    svc.execute_node(run['id'],'a');svc.execute_node(run['id'],'b');out=svc.finish(run['id'])
    assert out['status']=='succeeded' and out['nodes']['b']['output']['data']=={'text':'complete content'}
    assert '/b/attempt-1/inputs/' in out['nodes']['b']['inputs']['file']['path']


def test_oversized_complete_data_transparently_mapped(tmp_path):
    svc=service(tmp_path);graph=simple_graph({'rows':{'source':'node','node_id':'a','path':'rows'}})
    graph['nodes'][0]['source']='def main(inputs): return {"rows":["x"*100 for _ in range(12000)]}'
    graph['nodes'][1]['source']='def main(inputs): return {"count":len(inputs["rows"])}'
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id'])
    svc.execute_node(run['id'],'a');svc.execute_node(run['id'],'b');out=svc.finish(run['id'])
    assert out['status']=='succeeded'
    assert out['nodes']['a']['output']['data_ref']['size']>1024*1024
    assert out['nodes']['b']['output']['data']=={'count':12000}


def test_node_test_http_and_sample_authorization(tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from taskconsole.workflows import register_workflow_routes
    from taskconsole.store import Store
    app=FastAPI();svc=register_workflow_routes(app,Store(tmp_path,'sqlite:///'+str(tmp_path/'http.sqlite')),lambda request,tx,admin=False:{'id':'owner','role':'admin'})
    wf=svc.save(simple_graph())
    with TestClient(app) as client:
        response=client.post(f'/api/workflows/{wf["id"]}/nodes/b/test',json={'inputs':{'v':42}})
        assert response.status_code==202,response.text
        rid=response.json()['id'];svc.execute_node(rid,'b');svc.finish(rid)
        sample=client.get(f'/api/workflow-runs/{rid}/nodes/b/sample')
        assert sample.status_code==200 and sample.json()['inputs']=={'v':42}
        other=svc.save(simple_graph())
        assert client.post(f'/api/workflows/{other["id"]}/nodes/b/test',json={'sample_run_id':rid}).status_code==422


def test_api_trigger_schema_pin_idempotency_and_queue(tmp_path):
    svc=service(tmp_path);wf=svc.save(simple_graph());one=svc.publish(wf['id'])
    assert hasattr(svc,'trigger_run'),'trigger execution missing'
    wf['triggers']=[{'id':'api','kind':'api','enabled':True,'version_id':one['version_id'],'overlap':'queue','queue_limit':1,'parameter_schema':{'type':'object','required':['n'],'properties':{'n':{'type':'integer'}}}}]
    svc.save(wf,wf['id']);svc.publish(wf['id'])
    with pytest.raises(ValueError):svc.trigger_run(wf['id'],'api',{'params':{'n':'bad'}})
    first=svc.trigger_run(wf['id'],'api',{'params':{'n':1},'idempotency_key':'one'})
    assert svc.trigger_run(wf['id'],'api',{'params':{'n':2},'idempotency_key':'one'})['id']==first['id']
    second=svc.trigger_run(wf['id'],'api',{'params':{'n':2}})
    assert second['version_id']==one['version_id']
    with pytest.raises(ValueError):svc.trigger_run(wf['id'],'api',{'params':{'n':3}})
    svc.cancel(first['id']);svc.cancel(second['id'])
    wf['triggers'][0]['enabled']=False;svc.save(wf,wf['id'])
    with pytest.raises(ValueError,match='disabled'):svc.trigger_run(wf['id'],'api',{'params':{'n':1}})


def test_graph_entrance_claim_has_one_execution_owner(tmp_path):
    svc=service(tmp_path);wf=svc.save(simple_graph());svc.publish(wf['id']);run=svc.admit(wf['id'])
    svc.claim_graph(run['id'],'n8n-1');svc.claim_graph(run['id'],'n8n-1')
    with pytest.raises(ValueError,match='owns'):svc.claim_graph(run['id'],'n8n-2')


def test_latest_missed_once_within_grace_and_disabled_pending(tmp_path):
    from datetime import datetime,timezone,timedelta
    svc=service(tmp_path);wf=svc.save(simple_graph());svc.publish(wf['id'])
    wf['triggers']=[{'id':'daily','kind':'scheduled','enabled':True,'schedule':{'kind':'interval','every':1,'unit':'minutes','anchor':'2026-09-17T00:00:00+00:00'},'timezone':'UTC','missed_policy':'latest_once','grace_seconds':7200}]
    svc.save(wf,wf['id']);start=datetime(2026,9,17,tzinfo=timezone.utc);svc.tick(start)
    admitted=svc.tick(start+timedelta(minutes=10,seconds=45))
    assert len(admitted)==1
    assert admitted[0]['idempotency_key'].endswith('00:10:00+00:00')
    assert svc.tick(start+timedelta(minutes=10,seconds=46))==[]


def test_compiler_uses_short_requests_wait_status_and_entrance_claim(tmp_path):
    from taskconsole.workflows_n8n import compile_graph
    svc=service(tmp_path);wf=svc.save(simple_graph());svc.publish(wf['id']);run=svc.admit(wf['id'])
    graph=compile_graph(run,'http://127.0.0.1:8080')
    assert any(n['type']=='n8n-nodes-base.wait' for n in graph['nodes'])
    assert any(n['name']=='claim' for n in graph['nodes'])
    assert all(n['parameters']['options']['timeout']<=10000 for n in graph['nodes'] if n['type']=='n8n-nodes-base.httpRequest')


def test_queue_never_dispatches_same_workflow_concurrently(tmp_path,monkeypatch):
    from taskconsole import workflows_n8n as adapter
    svc=service(tmp_path);wf=svc.save(simple_graph());svc.publish(wf['id']);first=svc.admit(wf['id']);second=svc.admit(wf['id'],overlap='queue')
    class HeldThread:
        def __init__(self,*args,**kwargs):pass
        def start(self):pass
    monkeypatch.setattr(adapter.threading,'Thread',HeldThread)
    assert adapter.dispatch_pending(svc)==[first['id']]
    assert adapter.dispatch_pending(svc)==[]


def test_credentials_encrypted_resolved_at_execution_masked_and_authorized(tmp_path):
    svc=service(tmp_path)
    assert hasattr(svc,'save_credential'),'workflow credential vault missing'
    cred=svc.save_credential({'name':'Synthetic token','value':'synthetic-secret-123','allowed_workflows':[]})
    graph=simple_graph();graph['nodes']=graph['nodes'][:1];graph['edges']=[]
    graph['nodes'][0]['inputs']={'token':{'source':'credential','credential_id':cred['id']}}
    graph['nodes'][0]['source']='def main(inputs):\n print(inputs["token"])\n return {"token":inputs["token"],"length":len(inputs["token"])}'
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id'])
    newer=svc.save_credential({'value':'rotated-secret-456'},cred['id'])
    svc.execute_node(run['id'],'a');out=svc.finish(run['id']);state=out['nodes']['a']
    assert out['status']=='succeeded' and state['output']['data']['length']==18
    assert state['inputs']['token']=='[redacted]' and 'rotated-secret' not in str(out)
    assert state['credential_revisions'][cred['id']]==newer['revision']
    with svc.store.transaction() as tx:stored=tx.get('workflow_credential',cred['id'])
    assert 'rotated-secret' not in str(stored)
    svc.save_credential({'allowed_workflows':['another-workflow']},cred['id'])
    run=svc.admit(wf['id']);svc.execute_node(run['id'],'a');assert svc.finish(run['id'])['status']=='failed'


def test_transfer_credential_references_require_rebinding_and_retry_roundtrip(tmp_path):
    from taskconsole.workflows_transfer import export_template,import_template
    svc=service(tmp_path);cred=svc.save_credential({'name':'Private','value':'test-secret-value'})
    graph=simple_graph();graph['nodes'][0]['inputs']={'token':{'source':'credential','credential_id':cred['id']}}
    graph['nodes'][0]['config']['retry']={'max_attempts':2,'safe_to_retry':True}
    wf=svc.save(graph);template=export_template(svc.store,wf['id'])
    assert cred['id'] not in str(template) and 'test-secret-value' not in str(template)
    assert template['workflow']['nodes'][0]['config']['retry']['max_attempts']==2
    imported=import_template(svc.store,template)
    assert imported['requires_rebinding'] and imported['nodes'][0]['inputs']['token']['source']=='credential'


def test_run_name_freezes_allowed_parameters(tmp_path):
    svc=service(tmp_path);graph=simple_graph();graph['run_name_template']='{workflow} / {date} / {params.customer}'
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id'],{'customer':'Ada'})
    assert run.get('name','').startswith('Mapping test / ') and run['name'].endswith(' / Ada')


def test_migration_and_restored_versions_block_production_admission(tmp_path):
    svc=service(tmp_path);wf=svc.save(simple_graph());publication=svc.publish(wf['id'])
    with svc.store.transaction() as tx:
        record=tx.get('workflow',wf['id']);record['migration']={'task_id':'old','handoff_complete':False};tx.put('workflow',record)
    with pytest.raises(ValueError,match='handoff'):svc.admit(wf['id'])
    with svc.store.transaction() as tx:
        record=tx.get('workflow',wf['id']);record['migration']['handoff_complete']=True;tx.put('workflow',record)
        version=tx.get('workflow_version',publication['version_id']);version['restore_requires_rebuild']=True;tx.put('workflow_version',version)
    with pytest.raises(ValueError,match='rebuild'):svc.admit(wf['id'])


def test_artifact_tampering_and_outside_run_rejected(tmp_path):
    from taskconsole.workflows_execution import verified_file
    import hashlib
    svc=service(tmp_path);path=tmp_path/'outside.txt';path.write_text('hello')
    item={'path':str(path),'size':5,'sha256':hashlib.sha256(b'hello').hexdigest()}
    with pytest.raises(ValueError,match='outside'):verified_file(svc.store,'run',item)
    path=tmp_path/'workflow-runs'/'run'/'a'/'file.txt';path.parent.mkdir(parents=True);path.write_text('other')
    item['path']=str(path)
    with pytest.raises(ValueError,match='checksum'):verified_file(svc.store,'run',item)


def test_duplicate_adapter_invocation_cannot_fail_authoritative_run(tmp_path,monkeypatch):
    from taskconsole.workflows_n8n import execute_graph
    svc=service(tmp_path);wf=svc.save(simple_graph());svc.publish(wf['id']);run=svc.admit(wf['id'])
    with svc.store.transaction() as tx:
        record=tx.get('workflow_run',run['id']);record.update(status='running',adapter_execution_owner='authoritative');tx.put('workflow_run',record)
    monkeypatch.setenv('SLEEP_IN_N8N_COMMAND','["/definitely/unavailable"]')
    execute_graph(svc,run['id'])
    assert svc.get_run(run['id'])['status']=='running'


def test_sql_large_result_exports_full_dataset_for_downstream(tmp_path):
    svc=service(tmp_path);graph=svc.templates()[0];graph['nodes']=graph['nodes'][:2];graph['edges']=graph['edges'][:1]
    graph['nodes'][0]['source']="WITH RECURSIVE seq(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM seq WHERE n<12000) SELECT n, printf('%0100d',n) AS payload FROM seq"
    graph['nodes'][1]['source']='def main(inputs): return {"summary":{"count":len(inputs["orders"]),"total":"0"}}'
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id']);svc.execute_node(run['id'],'orders');svc.execute_node(run['id'],'summary');result=svc.finish(run['id'])
    assert result['status']=='succeeded'
    assert result['nodes']['summary']['output']['data']['summary']['count']==12000
    assert result['nodes']['orders']['output']['data_ref']['size']>1024*1024


def test_runtime_language_defaults_freeze_into_publication(tmp_path):
    from taskconsole.workflows_packs import RuntimePacks
    svc=service(tmp_path);packs=RuntimePacks(svc.store);profile=packs.create({'name':'Selected Python','language':'python','config':{}});version=packs.build(profile['id'])
    graph=simple_graph();graph['settings']={'runtime_defaults':{'python':version['id']}}
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id'])
    assert all(n['config']['runtime_version_id']==version['id'] for n in run['snapshot']['nodes'])


def test_migrated_dependencies_require_explicit_verified_runtime(tmp_path):
    svc=service(tmp_path);graph=simple_graph();graph['nodes'][0]['config']['migration_requires_runtime']=True
    wf=svc.save(graph)
    with pytest.raises(ValueError,match='explicit runtime'):svc.publish(wf['id'])


def test_disabled_trigger_discards_unadmitted_occurrence(tmp_path):
    from datetime import datetime,timezone,timedelta
    svc=service(tmp_path);wf=svc.save(simple_graph());svc.publish(wf['id'])
    wf['triggers']=[{'id':'disabled','kind':'scheduled','enabled':False,'schedule':{'kind':'daily','time':'07:30'},'timezone':'UTC'}];svc.save(wf,wf['id'])
    with svc.store.transaction() as tx:tx.put('workflow_occurrence',{'id':'pending','workflow_id':wf['id'],'trigger_id':'disabled','status':'pending','occurrence':'2026-09-17T07:30:00+00:00','params':{},'version_id':svc.store and wf.get('published_version_id')})
    assert svc.tick(datetime(2026,9,17,7,31,tzinfo=timezone.utc))==[]
    with svc.store.transaction() as tx:item=tx.get('workflow_occurrence','pending')
    assert item['status']=='skipped' and item['reason']=='trigger_disabled'


def test_artifact_input_total_quota_is_bounded(tmp_path):
    from taskconsole import workflows_execution as execution
    import hashlib
    svc=service(tmp_path);root=tmp_path/'workflow-runs'/'run';directory=root/'b'/'attempt-1';source=root/'a'/'file';source.parent.mkdir(parents=True);source.write_bytes(b'12345')
    item={'id':'file','node_id':'a','name':'a.txt','path':str(source),'size':5,'sha256':hashlib.sha256(b'12345').hexdigest(),'mediaType':'text/plain'}
    directory.joinpath('inputs').mkdir(parents=True);directory.joinpath('inputs','existing').write_bytes(b'12345')
    original=execution.MAX_DATA;execution.MAX_DATA=8
    try:
        with pytest.raises(ValueError,match='quota'):execution.materialize_artifact(svc.store,{'id':'run','artifacts':[item]},{'node_id':'a','name':'a.txt'},directory)
    finally:execution.MAX_DATA=original


def test_explicit_workflow_runtime_default_does_not_require_host_detector(tmp_path,monkeypatch):
    from taskconsole.workflows_packs import RuntimePacks
    from taskconsole import workflows
    svc=service(tmp_path);packs=RuntimePacks(svc.store);profile=packs.create({'name':'Python pack','language':'python','config':{}});version=packs.build(profile['id'])
    graph=simple_graph();graph['settings']={'runtime_defaults':{'python':version['id']}}
    wf=svc.save(graph)
    monkeypatch.setattr(workflows,'runtimes',lambda:[{'language':'python','status':'unavailable','reason':'Host detector unavailable'}])
    svc.publish(wf['id'])


def test_source_project_can_publish_without_duplicate_inline_source(tmp_path):
    from taskconsole.workflows_projects import SourceProjects
    svc=service(tmp_path);project=SourceProjects(svc.store).create({'name':'Main','language':'python','entrypoint':'main.py','files':{'main.py':'def main(inputs): return {"value":7}'}})
    graph=simple_graph();graph['nodes']=graph['nodes'][:1];graph['edges']=[];graph['nodes'][0].update(source='',config={'project_id':project['id']})
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id']);svc.execute_node(run['id'],'a')
    assert svc.finish(run['id'])['nodes']['a']['output']['data']=={'value':7}


def test_source_project_template_embeds_files_without_private_directory(tmp_path):
    from taskconsole.workflows_projects import SourceProjects
    from taskconsole.workflows_transfer import export_template,import_template
    svc=service(tmp_path);project=SourceProjects(svc.store).create({'name':'Portable','language':'python','entrypoint':'main.py','files':{'main.py':'def main(inputs): return {"v":42}','shared.py':'VALUE=7'}})
    graph=simple_graph();graph['nodes']=graph['nodes'][:1];graph['edges']=[];graph['nodes'][0].update(source='',config={'project_id':project['id']})
    wf=svc.save(graph);template=export_template(svc.store,wf['id'])
    assert project['id'] not in str(template) and str(tmp_path) not in str(template)
    restored=import_template(svc.store,template)
    assert restored['nodes'][0]['config'].get('project_id')
    svc.publish(restored['id']);run=svc.admit(restored['id']);svc.execute_node(run['id'],'a')
    assert svc.finish(run['id'])['nodes']['a']['output']['data']=={'v':42}


def test_workflow_notification_and_retention_policy_validation(tmp_path):
    svc=service(tmp_path);graph=simple_graph();graph['notifications']={'channel_ids':['missing'],'events':['failed']}
    with pytest.raises(ValueError,match='channel'):svc.save(graph)
    graph['notifications']={'channel_ids':[],'events':['not-an-event']}
    with pytest.raises(ValueError,match='event'):svc.save(graph)
    graph['notifications']={};graph['retention']={'success_days':30,'failure_days':30,'metadata_days':1}
    with pytest.raises(ValueError):svc.save(graph)


def test_recovery_terminates_only_verified_detached_worker(tmp_path):
    import subprocess,sys,os
    from taskconsole import workflows_execution as execution
    from taskconsole.workflows_n8n import recover_interrupted
    svc=service(tmp_path);wf=svc.save(simple_graph());svc.publish(wf['id']);run=svc.admit(wf['id'])
    assert hasattr(execution,'process_identity'),'worker identity persistence missing'
    directory=tmp_path/'workflow-runs'/run['id']/'a'/'attempt-1';directory.mkdir(parents=True)
    proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],cwd=directory,start_new_session=True)
    try:
        identity=execution.process_identity(proc.pid)
        assert identity
        with svc.store.transaction() as tx:
            record=tx.get('workflow_run',run['id']);record['status']='running';record['nodes']['a'].update(status='running',worker_pid=proc.pid,worker_identity=identity);tx.put('workflow_run',record)
        recover_interrupted(svc)
        proc.wait(timeout=3)
        assert proc.returncode<0 and svc.get_run(run['id'])['status']=='failed'
    finally:
        if proc.poll() is None:os.killpg(proc.pid,9);proc.wait()


def test_same_artifact_can_bind_two_named_inputs_without_second_copy(tmp_path):
    from taskconsole.workflows_execution import materialize_artifact
    import hashlib
    svc=service(tmp_path);root=tmp_path/'workflow-runs'/'run';source=root/'a'/'file';source.parent.mkdir(parents=True);source.write_bytes(b'hello')
    item={'id':'file','node_id':'a','name':'hello.txt','path':str(source),'size':5,'sha256':hashlib.sha256(b'hello').hexdigest(),'mediaType':'text/plain'}
    run={'id':'run','artifacts':[item]};binding={'node_id':'a','name':'hello.txt'};directory=root/'b'/'attempt-1'
    first=materialize_artifact(svc.store,run,binding,directory)
    assert materialize_artifact(svc.store,run,binding,directory)==first


def test_known_credential_is_masked_in_downloadable_text_artifacts(tmp_path):
    svc=service(tmp_path);cred=svc.save_credential({'name':'Synthetic','value':'known-artifact-secret'})
    graph=simple_graph();graph['nodes']=graph['nodes'][:1];graph['edges']=[]
    graph['nodes'][0].update(config={'entry_mode':'file'},inputs={'token':{'source':'credential','credential_id':cred['id']}},source='import os,json,pathlib\ni=json.load(open(os.environ["SLEEP_IN_INPUT_FILE"]))\npathlib.Path(os.environ["SLEEP_IN_ARTIFACT_DIR"],"note.txt").write_text(i["token"])\njson.dump({"schemaVersion":1,"data":{},"artifacts":[{"name":"note.txt","mediaType":"text/plain"}]},open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))')
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id']);svc.execute_node(run['id'],'a');result=svc.finish(run['id'])
    from pathlib import Path
    assert Path(result['artifacts'][0]['path']).read_text()=='[redacted]'
