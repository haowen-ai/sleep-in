import json
from pathlib import Path
import pytest
from taskconsole.store import Store


def test_encrypted_backup_restores_secrets_sources_and_disabled_schedules(tmp_path):
    from taskconsole.workflows_backup import create_backup, inspect_backup, restore_backup
    old=Store(tmp_path/'old','sqlite:///'+str(tmp_path/'old.sqlite'))
    with old.transaction() as tx:
        tx.put('user',{'id':'u1','username':'kept','password':'hashed-original'})
        tx.put('workflow',{'id':'w1','enabled':True,'triggers':[{'id':'daily','enabled':True}]})
        tx.put('variable',{'id':'v1','encrypted':old.fernet.encrypt(b'secret-original').decode()})
        tx.put('session',{'id':'must-expire'})
    file=old.path/'scripts'/'v1'/'main.py';file.parent.mkdir();file.write_text('print(42)')
    blob=create_backup(old,'long-enough-passphrase')
    assert b'secret-original' not in blob and b'hashed-original' not in blob
    assert inspect_backup(blob,'long-enough-passphrase')['counts']['workflow']==1
    with pytest.raises(ValueError):inspect_backup(blob,'wrong-password-123')
    new=Store(tmp_path/'new','sqlite:///'+str(tmp_path/'new.sqlite'))
    restore_backup(new,blob,'long-enough-passphrase')
    with new.transaction() as tx:
        assert tx.get('user','u1')['password']=='hashed-original'
        assert new.fernet.decrypt(tx.get('variable','v1')['encrypted'].encode())==b'secret-original'
        assert tx.get('workflow','w1')['enabled'] is False
        assert tx.get('workflow','w1')['triggers'][0]['enabled'] is False
        assert tx.all('session')==[]
    assert (new.path/'scripts'/'v1'/'main.py').read_text()=='print(42)'


def test_backup_rejects_active_and_restore_refuses_existing_installation(tmp_path):
    from taskconsole.workflows_backup import create_backup, restore_backup
    source=Store(tmp_path/'s','sqlite:///'+str(tmp_path/'s.sqlite'))
    with source.transaction() as tx:tx.put('workflow_run',{'id':'active','status':'running'})
    with pytest.raises(ValueError,match='active'):create_backup(source,'long-enough-password')
    with source.transaction() as tx:tx.remove('workflow_run','active')
    blob=create_backup(source,'long-enough-password')
    target=Store(tmp_path/'t','sqlite:///'+str(tmp_path/'t.sqlite'))
    with target.transaction() as tx:tx.put('user',{'id':'existing'})
    with pytest.raises(ValueError,match='fresh'):restore_backup(target,blob,'long-enough-password')


def test_backup_tampering_rejected_without_state_change(tmp_path):
    from taskconsole.workflows_backup import create_backup, restore_backup
    s=Store(tmp_path/'s','sqlite:///'+str(tmp_path/'s.sqlite'))
    blob=bytearray(create_backup(s,'long-enough-password'));blob[-40]^=1
    with pytest.raises(ValueError):restore_backup(s,bytes(blob),'long-enough-password')
    with s.transaction() as tx:assert tx.get('meta','settings')['schema']==1


def test_restore_holds_supervisor_lock_until_database_commit(tmp_path,monkeypatch):
    from taskconsole.workflows_backup import create_backup,restore_backup
    from taskconsole.local import InstanceLock
    from taskconsole.store import Transaction
    source=Store(tmp_path/'source','sqlite:///'+str(tmp_path/'s.sqlite'))
    target=Store(tmp_path/'target','sqlite:///'+str(tmp_path/'t.sqlite'))
    with source.transaction() as tx:tx.put('workflow',{'id':'w1','enabled':False})
    blob=create_backup(source,'long-enough-password')
    original=Transaction.put;observed=[]
    def put(tx,kind,row):
        if kind=='workflow':
            try:
                with InstanceLock(target.path):observed.append('unlocked')
            except RuntimeError:observed.append('locked')
        return original(tx,kind,row)
    monkeypatch.setattr(Transaction,'put',put)
    restore_backup(target,blob,'long-enough-password')
    assert observed==['locked']
    with InstanceLock(target.path):pass


def test_restore_preserves_business_strings_and_invalidates_missing_toolchains(tmp_path):
    from taskconsole.workflows_backup import create_backup,restore_backup
    s=Store(tmp_path/'source','sqlite:///'+str(tmp_path/'s.sqlite'));t=Store(tmp_path/'target','sqlite:///'+str(tmp_path/'t.sqlite'))
    literal=str(s.path)+'/business-value'
    with s.transaction() as tx:
        tx.put('workflow',{'id':'w','enabled':False,'params':{'message':literal}})
        tx.put('runtime_toolchain',{'id':'jdk','status':'ready','home':str(s.path/'toolchains/jdk'),'executable':str(s.path/'toolchains/jdk/bin/java')})
    restore_backup(t,create_backup(s,'long-enough-password'),'long-enough-password')
    with t.transaction() as tx:
        assert tx.get('workflow','w')['params']['message']==literal
        assert tx.get('runtime_toolchain','jdk')['status']=='failed'


def test_restore_failure_rolls_back_files_while_holding_lock(tmp_path,monkeypatch):
    from taskconsole.workflows_backup import create_backup,restore_backup
    from taskconsole.local import InstanceLock
    from taskconsole.store import Transaction
    import taskconsole.workflows_backup as backup
    s=Store(tmp_path/'s','sqlite:///'+str(tmp_path/'s.sqlite'));t=Store(tmp_path/'t','sqlite:///'+str(tmp_path/'t.sqlite'))
    (s.path/'scripts/main.py').write_text('print(1)')
    with s.transaction() as tx:tx.put('workflow',{'id':'w','enabled':False})
    blob=create_backup(s,'long-enough-password');original_put=Transaction.put;original_remove=backup.shutil.rmtree;observed=[]
    def put(tx,kind,row):
        if kind=='workflow':raise RuntimeError('injected transaction failure')
        return original_put(tx,kind,row)
    def remove(path,*args,**kwargs):
        if Path(path)==t.path/'scripts':
            try:
                with InstanceLock(t.path):observed.append('unlocked')
            except RuntimeError:observed.append('locked')
        return original_remove(path,*args,**kwargs)
    monkeypatch.setattr(Transaction,'put',put);monkeypatch.setattr(backup.shutil,'rmtree',remove)
    with pytest.raises(RuntimeError,match='injected'):restore_backup(t,blob,'long-enough-password')
    assert observed==['locked']
    assert (t.path/'scripts').is_dir() and list((t.path/'scripts').iterdir())==[]
    with t.transaction() as tx:assert tx.all('workflow')==[]


def test_backup_omits_managed_ephemeral_node_modules_link(tmp_path):
    from taskconsole.workflows_backup import create_backup,inspect_backup
    s=Store(tmp_path,'sqlite:///'+str(tmp_path/'state.sqlite'))
    dependency=s.path/'runtime-packs/v/node_modules';dependency.mkdir(parents=True)
    project=s.path/'workflow-runs/r/n/attempt-1/project';project.mkdir(parents=True)
    (project/'node_modules').symlink_to(dependency,target_is_directory=True)
    (project.parent/'output.json').write_text('{}')
    blob=create_backup(s,'long-enough-password')
    assert inspect_backup(blob,'long-enough-password')['files']==1


def test_backup_rejects_symlink_folder_root(tmp_path):
    from taskconsole.workflows_backup import create_backup
    s=Store(tmp_path/'state','sqlite:///'+str(tmp_path/'state.sqlite'))
    outside=tmp_path/'outside';outside.mkdir();(outside/'secret.txt').write_text('not managed')
    (s.path/'scripts').rmdir();(s.path/'scripts').symlink_to(outside,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):create_backup(s,'long-enough-password')
