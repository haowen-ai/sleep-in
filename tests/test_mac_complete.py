import importlib.util
import json
from pathlib import Path
from unittest.mock import patch
import pytest

ROOT=Path(__file__).parents[1]

def test_power_parser_handles_ac_battery_low_without_activation_gate():
    from taskconsole.local import parse_power
    assert parse_power("Now drawing from 'Battery Power'\n -InternalBattery-0 9%; discharging; 0:45 remaining present: true")=={'source':'battery','percent':9,'low':True}
    assert parse_power("Now drawing from 'AC Power'\n -InternalBattery-0 80%; charging;")=={'source':'ac','percent':80,'low':False}
    assert parse_power('unavailable')=={'source':'unknown','percent':None,'low':False}

def test_update_failed_health_rolls_back_bundle_and_data(tmp_path):
    from taskconsole.local_update import update_bundle
    current=tmp_path/'Sleep In.app';current.mkdir();(current/'version').write_text('old')
    candidate=tmp_path/'new.app';candidate.mkdir();(candidate/'version').write_text('new')
    install=tmp_path/'install';state=install/'state';state.mkdir(parents=True);(state/'data').write_text('kept')
    events=[]
    def command(bundle,action):
        events.append((Path(bundle).name,action))
        if action=='--launch':
            if (Path(bundle)/'version').read_text()=='new':
                (state/'data').write_text('new schema');return (1,'not ready')
            assert (state/'data').read_text()=='kept'
            return (0,'ready')
        return (0,'')
    result=update_bundle(candidate,current,install,verify=lambda _:None,command=command,wait_stopped=lambda:True)
    assert result['status']=='rolled_back';assert (current/'version').read_text()=='old';assert (state/'data').read_text()=='kept'
    assert result['backup'] and events.count(('Sleep In.app','--stop-finish'))==1

def test_update_success_preserves_stopped_preference_and_verifies_before_stop(tmp_path):
    from taskconsole.local_update import update_bundle
    current=tmp_path/'Sleep In.app';current.mkdir();(current/'version').write_text('old')
    candidate=tmp_path/'new.app';candidate.mkdir();(candidate/'version').write_text('new')
    install=tmp_path/'install';state=install/'state';state.mkdir(parents=True);(state/'local-preferences.json').write_text('{"running": false}')
    events=[]
    result=update_bundle(candidate,current,install,verify=lambda _:events.append('verified'),command=lambda _,a:(events.append(a) or (0,'')),wait_stopped=lambda:True)
    assert events[0]=='verified';assert '--launch' not in events;assert '--install-only' in events;assert result['status']=='updated';assert (current/'version').read_text()=='new'

def test_unsigned_candidate_rejected_before_any_stop_or_data_change(tmp_path):
    from taskconsole.local_update import update_bundle
    current=tmp_path/'Sleep In.app';current.mkdir();candidate=tmp_path/'new.app';candidate.mkdir();install=tmp_path/'install';install.mkdir()
    def reject(_):raise ValueError('Developer ID required')
    with pytest.raises(ValueError,match='Developer ID'):update_bundle(candidate,current,install,verify=reject,command=lambda *_:pytest.fail('must not stop'))

def test_distribution_build_signs_hardened_runtime_and_notarizes(tmp_path):
    spec=importlib.util.spec_from_file_location('build_mac',ROOT/'packaging/build_mac.py');module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    calls=[]
    with patch.object(module.subprocess,'run',side_effect=lambda args,**kw:calls.append(args)):
        module.build(tmp_path/'Sleep In.app',identity='Developer ID Application: Fixture (TESTTEAM)',notary_profile='fixture-profile')
    sign=next(args for args in calls if '/usr/bin/codesign'==args[0] and '--sign' in args)
    assert '--options' in sign and 'runtime' in sign and '--timestamp' in sign
    assert any('notarytool' in args and 'submit' in args and '--wait' in args for args in calls)
    assert any('stapler' in args and 'staple' in args for args in calls)

def test_native_next_schedule_is_read_only_and_disabled_triggers_are_ignored(tmp_path):
    from taskconsole.local import next_scheduled
    from taskconsole.store import Store
    store=Store(tmp_path,'sqlite:///'+str(tmp_path/'console.db'))
    with store.transaction() as tx:
        tx.put('workflow',{'id':'w','name':'Morning','enabled':False,'timezone':'UTC','triggers':[{'id':'on','kind':'scheduled','enabled':True,'schedule':{'kind':'daily','time':'09:00'}},{'id':'off','kind':'scheduled','enabled':False,'schedule':{'kind':'daily','time':'00:01'}}]})
    before=(tmp_path/'console.db').read_bytes()
    assert next_scheduled(tmp_path)['workflow']=='Morning'
    assert (tmp_path/'console.db').read_bytes()==before
    assert next_scheduled(tmp_path/'missing') is None
    assert not (tmp_path/'missing').exists()

def test_backup_verification_failure_restarts_unchanged_app(tmp_path):
    from taskconsole.local_update import update_bundle
    current=tmp_path/'Sleep In.app';current.mkdir();(current/'version').write_text('old')
    candidate=tmp_path/'new.app';candidate.mkdir();(candidate/'version').write_text('new')
    install=tmp_path/'install';(install/'state').mkdir(parents=True)
    calls=[]
    with patch('taskconsole.local_update.tree_manifest',side_effect=[{'a':'original'},{'a':'corrupt'}]):
        with pytest.raises(RuntimeError,match='backup verification'):
            update_bundle(candidate,current,install,verify=lambda _:None,command=lambda _,a:(calls.append(a) or (0,'')),wait_stopped=lambda:True)
    assert (current/'version').read_text()=='old'
    assert calls==['--stop-finish','--launch']

def test_signed_update_verification_rejects_signer_mismatch(tmp_path):
    from taskconsole.local_update import verify_signed_bundle
    import plistlib,subprocess
    bundle=tmp_path/'Candidate.app';(bundle/'Contents').mkdir(parents=True)
    (bundle/'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier':'local.sleepin.companion'}))
    with patch('taskconsole.local_update.signature_team',return_value='DIFFERENT'),patch('taskconsole.local_update.subprocess.run',return_value=subprocess.CompletedProcess([],0)):
        with pytest.raises(ValueError,match='signer differs'):verify_signed_bundle(bundle,'EXPECTED')

def test_update_lock_blocks_explicit_start_and_only_allows_owner_token(tmp_path,monkeypatch):
    import fcntl
    from taskconsole.local import assert_update_allowed,start
    install=tmp_path/'install';state=install/'state';state.mkdir(parents=True)
    lock_path=tmp_path/'.install-update.lock';token_path=tmp_path/'.install-update-token'
    token_path.write_text('{"token":"owner-only-fixture"}')
    with lock_path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(RuntimeError,match='update'):assert_update_allowed(state)
        with patch('taskconsole.local.subprocess.Popen',side_effect=AssertionError('must not launch')):
            with pytest.raises(RuntimeError,match='update'):start(state,explicit=True)
        monkeypatch.setenv('SLEEP_IN_UPDATE_TOKEN','owner-only-fixture');assert_update_allowed(state)
    monkeypatch.delenv('SLEEP_IN_UPDATE_TOKEN');assert_update_allowed(state)

def test_update_rejects_symlink_before_path_resolution(tmp_path):
    from taskconsole.local_update import update_bundle
    current=tmp_path/'Old.app';current.mkdir();candidate=tmp_path/'Real.app';candidate.mkdir();alias=tmp_path/'Alias.app';alias.symlink_to(candidate,target_is_directory=True)
    with pytest.raises(ValueError,match='symlink'):update_bundle(alias,current,tmp_path/'install',verify=lambda _:pytest.fail('verify must not receive dereferenced link'))

def test_saved_backup_manifest_matches_final_preferences(tmp_path):
    from taskconsole.local_update import update_bundle,tree_manifest
    current=tmp_path/'Sleep In.app';current.mkdir();(current/'version').write_text('old')
    candidate=tmp_path/'new.app';candidate.mkdir();(candidate/'version').write_text('new')
    install=tmp_path/'install';state=install/'state';state.mkdir(parents=True)
    def command(_,action):
        if action=='--stop-finish':(state/'local-preferences.json').write_text('{"running":false}')
        return 0,''
    result=update_bundle(candidate,current,install,verify=lambda _:None,command=command,wait_stopped=lambda:True)
    backup=Path(result['backup']);manifest=json.loads((backup/'manifest.json').read_text())
    assert manifest['installation']==tree_manifest(backup/'installation')
    assert manifest['restored_running_preference'] is True
    assert json.loads((backup/'installation/state/local-preferences.json').read_text())['running'] is True
    assert not (tmp_path/'.install-update-token').exists()
