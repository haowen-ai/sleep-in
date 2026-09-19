"""Owner recovery keeps graph references while requiring runtime review."""
import copy
import json

import pytest

from taskconsole.store import Store
from taskconsole.workflows import WorkflowService
from taskconsole.workflows_sql import create_connection,connect
from taskconsole.workflows_backup import create_backup,restore_backup


def test_encrypted_owner_recovery_preserves_published_graph_and_connections(tmp_path):
    source=Store(tmp_path/'source','sqlite:///'+str(tmp_path/'source.sqlite'))
    target=Store(tmp_path/'target','sqlite:///'+str(tmp_path/'target.sqlite'))
    service=WorkflowService(source)
    connection=create_connection(source,{'name':'Synthetic recovery','dialect':'sqlite','config':{'synthetic':True}})
    remote=create_connection(source,{'name':'Uncontacted private reference','dialect':'postgresql','config':{'host':'fixture.invalid','password':'synthetic-recovery-canary'}})
    graph={'name':'Restore graph','timezone':'America/Chicago','schedule':{'kind':'daily','time':'07:30'},'enabled':False,'params':{'region':'华东'},'nodes':[
        {'id':'sql','kind':'sql','source':'SELECT * FROM orders ORDER BY order_id','config':{'dialect':'sqlite','connection_id':connection['id'],'mode':'query'},'inputs':{}},
        {'id':'python','kind':'python','source':'def main(inputs): return {"count":len(inputs["orders"])}','inputs':{'orders':{'source':'node','node_id':'sql','path':['rows']}},'config':{}}],
        'edges':[{'source':'sql','target':'python'}]}
    wf=service.save(graph);published=service.publish(wf['id'])
    service.save({**wf,'enabled':True,'triggers':[{'id':'api','kind':'api','enabled':True}]},wf['id'])
    with source.transaction() as tx:
        before={kind:copy.deepcopy(tx.all(kind)) for kind in ['workflow','workflow_version','runtime_profile','runtime_version','connection']}
    blob=create_backup(source,'synthetic-owner-backup-passphrase')
    assert b'synthetic-recovery-canary' not in blob
    restore_backup(target,blob,'synthetic-owner-backup-passphrase')
    with target.transaction() as tx:
        restored=tx.get('workflow',wf['id']);version=tx.get('workflow_version',published['version_id'])
        assert restored['published_version_id']==published['version_id']
        assert restored['nodes']==before['workflow'][0]['nodes'] and restored['edges']==graph['edges']
        assert restored['params']==graph['params'] and restored['timezone']=='America/Chicago'
        assert restored['enabled'] is False and all(not t['enabled'] for t in restored['triggers'])
        assert version['restore_requires_rebuild'] is True
        original=before['workflow_version'][0]
        for prior,node in zip(original['snapshot']['nodes'],version['snapshot']['nodes']):
            for key in ['id','kind','source','inputs','config']:assert node.get(key)==prior.get(key)
        assert version['snapshot']['edges']==original['snapshot']['edges']
        profiles=tx.all('runtime_profile');runtimes=tx.all('runtime_version')
        assert {p['id'] for p in profiles}=={p['id'] for p in before['runtime_profile']}
        assert {r['id'] for r in runtimes}=={r['id'] for r in before['runtime_version']}
        assert runtimes and all(r['status']=='failed' and 'rebuild' in r['reason'] for r in runtimes)
        restored_connection=tx.get('connection',connection['id'])
        restored_remote=tx.get('connection',remote['id'])
        assert json.loads(target.fernet.decrypt(restored_remote['encrypted_config'].encode()))=={'host':'fixture.invalid','password':'synthetic-recovery-canary'}
        assert restored_remote['revision']==next(c for c in before['connection'] if c['id']==remote['id'])['revision']
        assert tx.get('meta','workflow_maintenance')['paused'] is True
    with connect(target,restored_connection) as db:
        assert db.execute('SELECT order_id FROM orders ORDER BY order_id').fetchall()==[('A001',),('A002',),('A003',)]
    with pytest.raises(ValueError,match='fresh'):
        restore_backup(source,blob,'synthetic-owner-backup-passphrase')
    with source.transaction() as tx:
        for kind,rows in before.items():assert tx.all(kind)==rows
    source.engine.dispose();target.engine.dispose()
