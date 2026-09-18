"""Interrupted build recovery uses real advisory locks, including child ownership."""
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import zipfile
import pytest
from taskconsole.store import Store
from taskconsole.workflows_packs import RuntimePacks
from taskconsole.workflows_toolchains import Toolchains

@pytest.fixture
def store(tmp_path):
    value=Store(tmp_path,'sqlite:///'+str(tmp_path/'state.sqlite'));yield value;value.engine.dispose()


def draft(store):
    packs=RuntimePacks(store);p=packs.create({'language':'shell'})
    with store.transaction() as tx:
        v=tx.get('runtime_version',p['latest_version_id']);v['status']='building';tx.put('runtime_version',v)
    return packs,p


def test_interrupted_runtime_build_becomes_retryable_and_builds_ready(store):
    packs,p=draft(store)
    visible=packs.version(p['latest_version_id'])
    assert visible['status']=='failed' and visible['recovery_required'] is True
    result=packs.build(p['id'])
    assert result['status']=='ready' and not result.get('recovery_required')


def test_active_runtime_build_lock_rejects_duplicate_without_changing_status(store):
    from taskconsole.workflows_build_lock import build_lock
    packs,p=draft(store)
    with build_lock(store.path,'runtime',p['latest_version_id']):
        assert packs.version(p['latest_version_id'])['status']=='building'
        with pytest.raises(ValueError,match='progress'):packs.build(p['id'])
    with store.transaction() as tx:assert tx.get('runtime_version',p['latest_version_id'])['status']=='building'


def archive_fixture(tmp_path):
    archive=tmp_path/'tool.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('fixture/bin/tool','synthetic test executable')
    return {'id':'fixture','name':'Fixture','language':'fixture','format':'zip','home_glob':'fixture','executable':'bin/tool','sha256':hashlib.sha256(archive.read_bytes()).hexdigest(),'url':'https://example.invalid/fixture.zip'},archive


def test_interrupted_archive_install_retries_but_active_install_is_locked(store,tmp_path,monkeypatch):
    from taskconsole.workflows_build_lock import build_lock
    manifest,archive=archive_fixture(tmp_path);tools=Toolchains(store)
    with store.transaction() as tx:tx.put('runtime_toolchain',{'id':'fixture','status':'installing','manifest':manifest})
    monkeypatch.setattr(subprocess,'run',lambda argv,**kwargs:subprocess.CompletedProcess(argv,0,'Fixture 1.0',''))
    with build_lock(store.path,'toolchain','fixture'):
        assert tools.list()['installed'][0]['status']=='installing'
        with pytest.raises(ValueError,match='progress'):tools.install_manifest(manifest,archive)
    assert tools.list()['installed'][0]['status']=='failed'
    result=tools.install_manifest(manifest,archive)
    assert result['status']=='ready'


def test_child_inherits_build_lock_after_parent_context_exits(store,tmp_path):
    from taskconsole.workflows_build_lock import build_lock,subprocess_lock_options
    import time
    marker=tmp_path/'child-ready'
    with build_lock(store.path,'runtime','child'):
        child=subprocess.Popen([sys.executable,'-c','import pathlib,time;pathlib.Path('+repr(str(marker))+').write_text("ready");time.sleep(30)'],**subprocess_lock_options())
        deadline=time.monotonic()+5
        while not marker.exists() and time.monotonic()<deadline:time.sleep(.01)
        assert marker.exists()
    try:
        with pytest.raises(ValueError,match='progress'):
            with build_lock(store.path,'runtime','child'):pass
    finally:child.terminate();child.wait(timeout=5)
    with build_lock(store.path,'runtime','child'):pass


def test_interrupted_system_dialog_request_remains_pending_without_reopening(store,monkeypatch):
    import taskconsole.workflows_toolchains as module
    monkeypatch.setattr(module.platform,'system',lambda:'Darwin');monkeypatch.setattr(module.platform,'machine',lambda:'arm64')
    key='apple-command-line-tools-arm64'
    with store.transaction() as tx:tx.put('runtime_toolchain',{'id':key,'status':'installing','installer':'system_dialog','phase':'system_consent','requested_at':'2026-09-18T00:00:00+00:00'})
    calls=[]
    def run(argv,**kwargs):
        calls.append(argv);assert '--install' not in argv
        return subprocess.CompletedProcess(argv,1,'','not installed')
    monkeypatch.setattr(subprocess,'run',run)
    tools=Toolchains(store);assert tools.list()['installed'][0]['status']=='pending'
    assert tools.install(key)['status']=='pending'
    assert calls==[['/usr/bin/xcode-select','-p']]
