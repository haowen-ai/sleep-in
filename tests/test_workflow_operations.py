import json
import threading
from datetime import timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from taskconsole.store import Store, now


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path, 'sqlite:///'+str(tmp_path/'test.sqlite'))


def operations(store):
    from taskconsole.workflows_operations import WorkflowOperations
    return WorkflowOperations(store)


def put_run(store, rid='r1', status='failed', test=False, days=0):
    with store.transaction() as tx:
        tx.put('workflow_run', {'id':rid,'workflow_id':'w1','workflow_name':'Morning',
            'status':status,'test':test,'created_at':(now()-timedelta(days=days)).isoformat(),
            'finished_at':(now()-timedelta(days=days)).isoformat(),
            'error':'failure with secret-123','nodes':{},'artifacts':[],
            'snapshot':{'notifications':{'channel_ids':['c1'],'events':['failed','recovery','succeeded']}}})


def test_channel_secret_is_write_only_and_preserved(store):
    op=operations(store)
    record=op.save_channel({'name':'Webhook','kind':'webhook','config':{'url':'http://127.0.0.1:1'},'secret':'secret-123'},'c1')
    assert record['has_secret'] is True and 'secret-123' not in json.dumps(record)
    op.save_channel({'name':'Renamed','kind':'webhook','config':{'url':'http://127.0.0.1:1'}},'c1')
    with store.transaction() as tx:
        stored=tx.get('workflow_channel','c1')
    assert store.fernet.decrypt(stored['secret'].encode()).decode()=='secret-123'


def test_real_webhook_deduplicated_masked_and_test_runs_suppressed(store):
    received=[]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(204);self.end_headers()
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        op=operations(store)
        op.save_channel({'name':'Local','kind':'webhook','config':{'url':f'http://127.0.0.1:{server.server_port}'},'secret':'secret-123'},'c1')
        put_run(store)
        op.on_run_terminal('r1');op.on_run_terminal('r1');op.tick();op.tick()
        assert len(received)==1
        assert received[0]['run_id']=='r1'
        assert 'secret-123' not in json.dumps(received[0])
        assert 'inputs' not in received[0]
        put_run(store,'r2',test=True);op.on_run_terminal('r2');op.tick()
        assert len(received)==1
        assert op.deliveries()[0]['status']=='sent'
    finally:server.shutdown();server.server_close()


def test_delivery_failure_never_changes_run_status(store):
    op=operations(store);op.save_channel({'name':'Unavailable','kind':'webhook','config':{'url':'http://127.0.0.1:1'}},'c1')
    put_run(store);op.on_run_terminal('r1');op.tick()
    with store.transaction() as tx:assert tx.get('workflow_run','r1')['status']=='failed'
    assert op.deliveries()[0]['status']=='failed'
    assert op.deliveries()[0]['attempts']==1


def test_recovery_is_only_after_a_failed_published_run(store):
    op=operations(store);op.save_channel({'name':'Local','kind':'webhook','config':{'url':'http://127.0.0.1:1'}},'c1')
    put_run(store,'r1',status='failed');op.on_run_terminal('r1')
    put_run(store,'r2',status='succeeded');op.on_run_terminal('r2')
    put_run(store,'r3',status='succeeded');op.on_run_terminal('r3')
    events=[d['event'] for d in op.deliveries()]
    assert events.count('recovery')==1
    assert events.count('succeeded')==1


def test_retention_protects_active_and_referenced_artifacts(store):
    op=operations(store)
    put_run(store,'old',days=40);put_run(store,'protected',days=40);put_run(store,'active',status='running',days=40)
    for rid in ('old','protected','active'):
        path=store.path/'workflow-runs'/rid;path.mkdir(parents=True);(path/'result.txt').write_text('result')
    with store.transaction() as tx:
        active=tx.get('workflow_run','active');active['input_run_ids']=['protected'];tx.put('workflow_run',active)
    assert op.retention_preview()['data_run_ids']==['old']
    op.cleanup()
    assert not (store.path/'workflow-runs'/'old').exists()
    assert (store.path/'workflow-runs'/'protected'/'result.txt').exists()
    assert (store.path/'workflow-runs'/'active'/'result.txt').exists()
    with store.transaction() as tx:assert tx.get('workflow_run','old')['data_expired'] is True


def test_invalid_channel_and_retention_rejected(store):
    op=operations(store)
    with pytest.raises(ValueError):op.save_channel({'name':'Bad','kind':'webhook','config':{'url':'file:///etc/passwd'}})
    with pytest.raises(ValueError):op.save_policy({'success_days':30,'failure_days':30,'metadata_days':1})
    with pytest.raises(ValueError):op.save_channel({'name':'Mail','kind':'email','config':{'host':'localhost','from':'a\nb','to':['x@test']}})


def test_webhook_url_token_and_other_vault_secrets_not_exposed(store):
    op=operations(store)
    row=op.save_channel({'name':'Secret endpoint','kind':'webhook','config':{'url':'https://example.test/hook/token-123?key=abcdef'}},'c1')
    assert 'token-123' not in json.dumps(row) and 'abcdef' not in json.dumps(row)
    with store.transaction() as tx:
        tx.put('variable',{'id':'v1','encrypted':store.fernet.encrypt(b'variable-secret').decode()})
        tx.put('connection',{'id':'c2','encrypted_config':store.fernet.encrypt(b'{"password":"db-secret"}').decode()})
    assert op._mask('variable-secret and db-secret')=='[redacted] and [redacted]'


def test_corrupt_retention_cannot_delete_metadata_while_data_is_retained(store):
    op=operations(store);put_run(store,'old',days=2)
    with store.transaction() as tx:
        r=tx.get('workflow_run','old');r['snapshot']['retention']={'success_days':90,'failure_days':90,'metadata_days':1};tx.put('workflow_run',r)
    assert op.retention_preview()['metadata_run_ids']==[]


def test_retention_transaction_failure_preserves_files_and_records(store,monkeypatch):
    from taskconsole.store import Transaction
    op=operations(store);put_run(store,'old',days=40)
    directory=store.path/'workflow-runs/old';directory.mkdir(parents=True);(directory/'result.txt').write_text('preserve')
    original=Transaction.put
    def failing(tx,kind,row):
        if kind=='workflow_run' and row.get('data_expired'):raise RuntimeError('injected retention failure')
        return original(tx,kind,row)
    monkeypatch.setattr(Transaction,'put',failing)
    with pytest.raises(RuntimeError,match='injected'):op.cleanup()
    assert (directory/'result.txt').read_text()=='preserve'
    with store.transaction() as tx:assert not tx.get('workflow_run','old').get('data_expired')


def test_retention_rejects_symlink_managed_root(store,tmp_path):
    op=operations(store);put_run(store,'old',days=40)
    outside=tmp_path/'outside';(outside/'old').mkdir(parents=True);(outside/'old/protected.txt').write_text('safe')
    (store.path/'workflow-runs').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):op.cleanup()
    assert (outside/'old/protected.txt').read_text()=='safe'


def test_retention_reconciles_staged_uncommitted_cleanup(store):
    op=operations(store);put_run(store,'recent',days=1)
    staged=store.path/'.workflow-retention-trash/recent';staged.mkdir(parents=True);(staged/'keep.txt').write_text('restore')
    op.cleanup()
    assert (store.path/'workflow-runs/recent/keep.txt').read_text()=='restore'
