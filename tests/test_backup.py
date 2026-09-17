from pathlib import Path
import json
import zipfile
import pytest
from taskconsole.app import create_app
from taskconsole.backup import backup,restore
from taskconsole.store import Store


def test_backup_excludes_secrets_and_restore_disables_schedules(tmp_path):
    source=create_app(tmp_path/'source','sqlite:///'+str(tmp_path/'source.db')).state.store
    with source.transaction() as tx:
        tx.put('variable',{'id':'secret','name':'TOKEN','encrypted':'private-value','scope':'instance'})
        tx.put('user',{'id':'owner','password':'passwordhash'})
        tx.put('task',{'id':'one','enabled':True,'next_run':'2027-01-01T00:00:00+00:00','revision':1})
    archive=tmp_path/'backup.zip'
    backup(source,archive)
    with zipfile.ZipFile(archive) as z:
        records=json.loads(z.read('records.json'))
        assert 'variable' not in records and 'user' not in records
        assert not any('token' in n or 'variable-key' in n for n in z.namelist())
    dest=Store(tmp_path/'dest','sqlite:///'+str(tmp_path/'dest.db'))
    restore(dest,archive)
    with dest.transaction() as tx:
        assert tx.get('task','one')['enabled'] is False
        assert tx.get('task','one')['next_run'] is None
        assert tx.all('user')==[]
    assert (dest.path/'scripts'/'sample-3-v1'/'main.py').is_file()


def test_restore_rejects_existing_tasks_and_traversal(tmp_path):
    store=Store(tmp_path/'dest','sqlite:///'+str(tmp_path/'test.db'))
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('../escape','bad');z.writestr('records.json','{}')
    with pytest.raises(ValueError):restore(store,archive)
    assert not (tmp_path/'escape').exists()


def test_restore_rejects_record_ids_that_can_escape_state_directories(tmp_path):
    store=Store(tmp_path/'dest','sqlite:///'+str(tmp_path/'test.db'))
    archive=tmp_path/'bad-id.zip'
    records={kind:[] for kind in ('meta','script','version','task','execution','audit')}
    records['version']=[{
        'id':'../../outside','script_id':'sample-1','status':'published','freeze':'',
        'runtime':{},'created_at':'2026-01-01T00:00:00+00:00'
    }]
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('records.json',json.dumps(records))

    with pytest.raises(ValueError,match='identifier'):
        restore(store,archive)

    assert not (tmp_path/'outside').exists()
    with store.transaction() as tx:
        assert tx.all('version')==[]


def test_restore_rejects_entries_nested_below_records_json(tmp_path):
    store=Store(tmp_path/'dest','sqlite:///'+str(tmp_path/'test.db'))
    archive=tmp_path/'bad-records-path.zip'
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('records.json/child','{}')

    with pytest.raises(ValueError,match='Unsafe backup path'):
        restore(store,archive)


def test_backup_rejects_destination_inside_included_run_tree(tmp_path):
    store=Store(tmp_path/'source','sqlite:///'+str(tmp_path/'source.db'))
    destination=store.path/'runs'/'backup.zip'

    with pytest.raises(ValueError,match='outside'):
        backup(store,destination,include_runs=True)

    assert not destination.exists()


def test_failed_backup_does_not_leave_partial_destination(tmp_path,monkeypatch):
    store=Store(tmp_path/'source','sqlite:///'+str(tmp_path/'source.db'))
    (store.path/'scripts'/'example.py').write_text('pass\n')
    destination=tmp_path/'backup.zip'

    def fail_write(self,*args,**kwargs):
        raise OSError('simulated storage failure')

    monkeypatch.setattr(zipfile.ZipFile,'write',fail_write)
    with pytest.raises(OSError,match='storage failure'):
        backup(store,destination)

    assert not destination.exists()
