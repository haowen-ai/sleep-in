import pytest
from taskconsole.store import Store, stamp


def fixture(tmp_path,schedule=None):
    s=Store(tmp_path,'sqlite:///'+str(tmp_path/'console.sqlite'))
    with s.transaction() as tx:
        tx.put('version',{'id':'v1','script_id':'s1','mode':'function','status':'published','freeze':''})
        tx.put('task',{'id':'t1','name':'Legacy task','version_id':'v1','params':{'name':'Alice'},'schedule':schedule or {'kind':'interval','every':15},'anchor':'2026-01-01T00:00:00+00:00','timezone':'UTC','timeout':100,'enabled':True,'revision':1})
    folder=s.path/'scripts'/'v1';folder.mkdir();(folder/'main.py').write_text('def main(params):\n    print(params["name"])\n')
    return s


def test_migration_disabled_idempotent_preserves_schedule_and_source(tmp_path):
    from taskconsole.workflows_migration import WorkflowMigration
    s=fixture(tmp_path);m=WorkflowMigration(s)
    first=m.convert('t1');second=m.convert('t1')
    assert first['workflow_id']==second['workflow_id']
    with s.transaction() as tx:
        wf=tx.get('workflow',first['workflow_id'])
        assert tx.get('task','t1')['enabled'] is True
    assert wf['enabled'] is False and wf['params']=={'name':'Alice','recipients':[]}
    assert wf['schedule']['anchor']=='2026-01-01T00:00:00+00:00'
    assert wf['migration']['handoff_complete'] is False
    assert wf['nodes'][0]['kind']=='python'
    m.handoff('t1')
    with s.transaction() as tx:
        assert tx.get('task','t1')['enabled'] is False
        assert tx.get('workflow',first['workflow_id'])['migration']['handoff_complete'] is True


def test_migration_cron_disabled_manual_and_active_handoff_rejected(tmp_path):
    from taskconsole.workflows_migration import WorkflowMigration
    s=fixture(tmp_path,{'kind':'cron','cron':'0 7 * * 1'});m=WorkflowMigration(s);result=m.convert('t1')
    with s.transaction() as tx:
        wf=tx.get('workflow',result['workflow_id'])
        tx.put('execution',{'id':'r1','task_id':'t1','status':'running'})
    assert wf['schedule']=={'kind':'manual'}
    assert wf['migration']['needs_schedule_review'] is True
    with pytest.raises(ValueError,match='active'):m.handoff('t1')


@pytest.mark.parametrize('asynchronous',[False,True])
def test_legacy_wrapper_executes_params_and_collects_artifact(tmp_path,asynchronous):
    from taskconsole.workflows_migration import WorkflowMigration
    from taskconsole.workflows_packs import prepare_node
    from taskconsole.workflows_runtime import run_script
    s=fixture(tmp_path)
    (s.path/'scripts/v1/main.py').write_text('import os\nfrom pathlib import Path\n'+('async ' if asynchronous else '')+'def main(params):\n    Path(os.environ["TASK_OUTPUT_DIR"],"hello.txt").write_text(params["name"])\n')
    result=WorkflowMigration(s).convert('t1')
    with s.transaction() as tx:wf=tx.get('workflow',result['workflow_id'])
    node=prepare_node(s,wf['nodes'][0])
    run=run_script(node,{'name':'Alice'},s.path/'test-run',s.path)
    assert run['status']=='succeeded'
    assert run['output']['artifacts'][0]['name']=='hello.txt'
    from pathlib import Path
    assert Path(run['output']['artifacts'][0]['path']).read_text()=='Alice'


def test_migration_preserves_scoped_variable_environment_without_plaintext_source(tmp_path):
    from taskconsole.workflows_migration import WorkflowMigration
    from taskconsole.workflows import WorkflowService
    import json
    s=fixture(tmp_path)
    with s.transaction() as tx:
        tx.put('variable',{'id':'global','scope':'instance','name':'API_TOKEN','encrypted':s.fernet.encrypt(b'global-value').decode()})
        tx.put('variable',{'id':'script','scope':'s1','name':'API_TOKEN','encrypted':s.fernet.encrypt(b'script-secret-value').decode()})
        version=tx.get('version','v1');version['manifest']={'required_variables':['API_TOKEN']};tx.put('version',version)
    (s.path/'scripts/v1/main.py').write_text('import os,json\nfrom pathlib import Path\ndef main(params):\n    assert os.environ["API_TOKEN"]=="script-secret-value"\n    assert params==json.loads(Path(os.environ["TASK_PARAMS_FILE"]).read_text())\n    assert params=={"name":"Alice","recipients":[]}\n    print(os.environ["API_TOKEN"])\n')
    result=WorkflowMigration(s).convert('t1');service=WorkflowService(s)
    with s.transaction() as tx:
        wf=tx.get('workflow',result['workflow_id']);credentials=tx.all('workflow_credential')
    assert len(credentials)==1 and credentials[0]['allowed_workflows']==[wf['id']]
    assert 'script-secret-value' not in json.dumps(wf)
    run=service.admit(wf['id'],test=True);state=service.execute_node(run['id'],'legacy')
    assert state['status']=='succeeded',state
    assert 'script-secret-value' not in state['stdout'] and '[redacted]' in state['stdout']


def test_migration_missing_required_variable_stops_before_creating_draft(tmp_path):
    from taskconsole.workflows_migration import WorkflowMigration
    s=fixture(tmp_path)
    with s.transaction() as tx:
        version=tx.get('version','v1');version['manifest']={'required_variables':['REQUIRED_TOKEN']};tx.put('version',version)
    with pytest.raises(ValueError,match='required variable'):WorkflowMigration(s).convert('t1')
    with s.transaction() as tx:assert tx.all('workflow')==[] and tx.all('source_project')==[]
