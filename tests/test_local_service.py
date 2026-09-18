"""Lifecycle tests never register login items or acquire real power assertions."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path
import pytest


def test_single_instance_lock_releases_after_owner_exits(tmp_path):
    from taskconsole.local import InstanceLock
    with InstanceLock(tmp_path):
        with pytest.raises(RuntimeError):
            with InstanceLock(tmp_path): pass
    with InstanceLock(tmp_path): pass


def test_explicit_stop_persists_and_login_does_not_override(tmp_path):
    from taskconsole.local import set_running_preference, should_start
    assert should_start(tmp_path)
    set_running_preference(tmp_path, False)
    assert not should_start(tmp_path)
    assert should_start(tmp_path, explicit=True)
    assert not should_start(tmp_path)


def test_service_status_never_reports_stale_pid_as_running(tmp_path):
    from taskconsole.local import status, write_json
    write_json(tmp_path/'local-status.json', {'pid':123,'state':'running','assertion':True})
    result = status(tmp_path)
    assert result['state'] == 'stopped'
    assert not result['assertion']


def test_environment_forces_loopback_and_has_consistent_state(tmp_path):
    from taskconsole.local import child_environment
    config={'app_port':18181,'n8n_command':['/node','/n8n']}
    env=child_environment(tmp_path,config,{'APP_HOST':'0.0.0.0','DATABASE_URL':'postgres://danger'})
    assert env['APP_HOST']=='127.0.0.1'
    assert env['SLEEP_IN_BASE_URL']=='http://127.0.0.1:18181'
    assert env['APP_STATE_DIR']==str(tmp_path.resolve())
    assert env['DATABASE_URL']==f'sqlite:///{tmp_path.resolve()}/console.db'
    assert json.loads(env['SLEEP_IN_N8N_COMMAND'])==['/node','/n8n']


def test_registration_requires_explicit_consent_without_writing(tmp_path):
    from taskconsole.local import register_login
    with pytest.raises(ValueError): register_login(tmp_path,consent=False)
    assert not list(tmp_path.iterdir())


def test_worker_readiness_requires_fresh_heartbeat_and_n8n(tmp_path):
    from taskconsole.local import worker_health
    from taskconsole.store import Store, stamp
    store=Store(tmp_path,f'sqlite:///{tmp_path}/console.db')
    assert worker_health(tmp_path)=='unavailable'
    with store.transaction() as tx:
        tx.put('meta',{'id':'workflow_worker','status':'ready','last_seen':stamp(),'n8n_available':True})
    assert worker_health(tmp_path)=='ready'
    with store.transaction() as tx:
        tx.put('meta',{'id':'workflow_worker','status':'ready','last_seen':'2000-01-01T00:00:00+00:00','n8n_available':True})
    assert worker_health(tmp_path)=='unavailable'
    store.engine.dispose()


def test_child_pipe_eof_stops_owned_process_group(tmp_path):
    from taskconsole.local import spawn_component, terminate_component
    target=tmp_path/'child.pid'
    source='import os,time,pathlib; pathlib.Path('+repr(str(target))+').write_text(str(os.getpid())); time.sleep(120)'
    import os
    with (tmp_path/'capture.txt').open('w') as log:
        process=spawn_component([sys.executable,'-c',source],dict(os.environ),log)
        try:
            deadline=time.monotonic()+10
            while not target.exists() and time.monotonic()<deadline: time.sleep(.05)
            assert target.exists()
            pid=int(target.read_text())
            terminate_component(process)
            with pytest.raises(ProcessLookupError): os.kill(pid,0)
        finally:
            if process.poll() is None: terminate_component(process)


def test_live_lock_does_not_trust_previous_generations_ready_status(tmp_path):
    from taskconsole.local import InstanceLock,status,write_json
    import os
    write_json(tmp_path/'local-status.json',{'pid':os.getpid(),'state':'running','assertion':True,'generation':'old'})
    with InstanceLock(tmp_path):
        result=status(tmp_path)
        assert result['state']=='starting'
        assert not result['assertion']


def test_launcher_rejects_unknown_action_without_network_install(tmp_path):
    import os
    root=Path(__file__).resolve().parents[1]
    install=tmp_path/'installation'
    result=subprocess.run(['/bin/bash',str(root/'launch-mac.command'),'--invalid'],env={**os.environ,'SLEEP_IN_INSTALL_DIR':str(install)},capture_output=True,text=True,timeout=5)
    assert result.returncode!=0
    assert 'Unknown launch action' in result.stderr
    assert not (install/'downloads').exists()


def test_stop_request_reports_draining_until_lock_is_released(tmp_path):
    from taskconsole.local import InstanceLock,status,request_stop,write_json
    import os
    with InstanceLock(tmp_path) as lock:
        write_json(tmp_path/'local-status.json',{'pid':os.getpid(),'generation':lock.generation,'state':'running','assertion':True,'seen_at':time.time()})
        result=request_stop(tmp_path,'finish')
        assert result['state']=='draining'
        assert result['assertion']
        assert result['running_preference'] is False
    assert status(tmp_path)['state']=='stopped'


def test_http_readiness_rejects_another_application_generation():
    from taskconsole.local import app_healthy
    from http.server import BaseHTTPRequestHandler,HTTPServer
    import threading
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200);self.end_headers();self.wfile.write(b'{"status":"ok","instance_id":"other"}')
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        url=f'http://127.0.0.1:{server.server_port}'
        assert not app_healthy(url,'mine')
        assert app_healthy(url,'other')
    finally:server.shutdown();thread.join();server.server_close()


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_TEST_INSTALL_DIR'),reason='Prepared isolated managed runtime fixture required')
def test_bootstrap_recovers_interrupted_installation_lock():
    import os
    root=Path(__file__).resolve().parents[1]
    install=Path(os.environ['SLEEP_IN_TEST_INSTALL_DIR'])
    lock=install/'installation.lock'
    lock.mkdir()
    (lock/'pid').write_text('99999999')
    try:
        result=subprocess.run(['/bin/bash',str(root/'launch-mac.command'),'--install-only'],env={**os.environ,'SLEEP_IN_INSTALL_DIR':str(install)},capture_output=True,text=True,timeout=30)
        assert result.returncode==0,result.stderr
        assert not lock.exists()
    finally:
        (lock/'pid').unlink(missing_ok=True)
        if lock.exists():lock.rmdir()


def test_live_lock_rejects_stale_matching_generation_health(tmp_path):
    from taskconsole.local import InstanceLock, status, write_json
    with InstanceLock(tmp_path) as lock:
        write_json(tmp_path/'local-status.json', {'pid':os.getpid(),'generation':lock.generation,
            'state':'running','assertion':True,'components':{'app':'ready','worker':'ready'},'seen_at':time.time()-120})
        result=status(tmp_path)
        assert result['state']=='degraded'
        assert result['assertion'] is False
        assert result['components']=={}
        assert 'stale' in result['reason'].lower()


def _launcher_function(name):
    source=(Path(__file__).resolve().parents[1]/'launch-mac.command').read_text()
    start=source.index(name+'() {')
    return source[start:source.index('\n}',start)+2]


def test_completed_download_partial_is_verified_and_reused_without_network(tmp_path):
    import hashlib
    from shlex import quote
    payload=b'complete synthetic runtime archive\n'
    destination=tmp_path/'archive.tar.gz'
    destination.with_suffix('.gz.partial').write_bytes(payload)
    call=tmp_path/'curl-called'
    fakebin=tmp_path/'bin';fakebin.mkdir()
    curl=fakebin/'curl';curl.write_text('#!/bin/sh\ntouch '+quote(str(call))+'\nexit 22\n');curl.chmod(0o700)
    script='set -euo pipefail\n'+_launcher_function('download_verified')+'\ndownload_verified "$1" "$2" "$3"\n'
    result=subprocess.run(['/bin/bash','-c',script,'download-test','https://invalid.example/synthetic',str(destination),hashlib.sha256(payload).hexdigest()],env={**os.environ,'PATH':str(fakebin)+os.pathsep+os.environ.get('PATH','/usr/bin:/bin')},capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert destination.read_bytes()==payload
    assert not call.exists()
    assert not destination.with_suffix('.gz.partial').exists()


def test_installer_repairs_stale_n8n_marker_without_real_downloads(tmp_path):
    import hashlib
    from shlex import quote
    root=Path(__file__).resolve().parents[1]
    install=tmp_path/'installation'
    python=install/'venv/bin/python';python.parent.mkdir(parents=True);python.symlink_to(sys.executable)
    (install/'python-requirements.sha256').write_text(hashlib.sha256((root/'requirements.txt').read_bytes()).hexdigest()+'\n')
    uv=install/'tools/uv-aarch64-apple-darwin/uv';uv.parent.mkdir(parents=True);uv.write_text('#!/bin/sh\nexit 0\n');uv.chmod(0o700)
    node=install/'tools/node-v24.13.0-darwin-arm64/bin/node';node.parent.mkdir(parents=True)
    target=install/'n8n/node_modules/n8n/bin/n8n';calls=tmp_path/'npm-called'
    node.write_text('#!/bin/bash\ncase "$1" in\n */npm-cli.js) shift; while [ "$#" -gt 0 ]; do if [ "$1" = --prefix ]; then prefix="$2"; break; fi; shift; done; mkdir -p "$prefix/node_modules/n8n/bin"; printf synthetic > "$prefix/node_modules/n8n/bin/n8n"; touch '+quote(str(calls))+' ;;\n *) [ -f "$1" ] || exit 1; echo 2.39.7 ;;\nesac\n');node.chmod(0o700)
    (install/'n8n-ready').touch()
    fakebin=tmp_path/'bin';fakebin.mkdir()
    for name,source in {'uname':'#!/bin/sh\n[ "$1" = "-s" ] && echo Darwin || echo arm64\n','sw_vers':'#!/bin/sh\necho 13.0\n','curl':'#!/bin/sh\necho "Unexpected real download" >&2\nexit 99\n'}.items():
        file=fakebin/name;file.write_text(source);file.chmod(0o700)
    # The synthetic installer must not inherit real native runtime configuration.
    synthetic_env={k:v for k,v in os.environ.items() if not k.startswith(('SLEEP_IN_','NODE'))}
    result=subprocess.run(['/bin/bash',str(root/'launch-mac.command'),'--install-only'],env={**synthetic_env,'PATH':str(fakebin)+os.pathsep+os.environ.get('PATH','/usr/bin:/bin'),'SLEEP_IN_INSTALL_DIR':str(install)},capture_output=True,text=True,timeout=15)
    assert result.returncode==0,result.stderr
    assert calls.exists(), 'Stale marker skipped reinstall of missing n8n'
    assert target.read_text()=='synthetic'
    assert (install/'n8n-ready').exists()


def test_unresumable_partial_retries_full_download_and_checks_hash(tmp_path):
    import hashlib
    from shlex import quote
    payload=b'verified replacement bytes\n'
    fixture=tmp_path/'fixture';fixture.write_bytes(payload)
    destination=tmp_path/'runtime.tar.gz';destination.with_suffix('.gz.partial').write_bytes(b'corrupt full-length prior partial')
    calls=tmp_path/'curl-count';fakebin=tmp_path/'bin';fakebin.mkdir()
    curl=fakebin/'curl'
    curl.write_text('#!/bin/bash\ncase \" $* \" in *\" --head \"*) echo \"Content-Length: 27\"; exit 0 ;; esac\nresume=false; output=""\nwhile [ "$#" -gt 0 ]; do case "$1" in --continue-at) resume=true; shift ;; --output) output="$2"; shift ;; esac; shift; done\necho call >> '+quote(str(calls))+'\nif [ "$resume" = true ]; then exit 22; fi\ncp '+quote(str(fixture))+' "$output"\n');curl.chmod(0o700)
    script='set -euo pipefail\nINSTALL_ROOT='+quote(str(tmp_path))+'\n'+_launcher_function('progress')+'\n'+_launcher_function('download_verified')+'\ndownload_verified "$1" "$2" "$3"\n'
    result=subprocess.run(['/bin/bash','-c',script,'download-test','https://invalid.example/synthetic',str(destination),hashlib.sha256(payload).hexdigest()],env={**os.environ,'PATH':str(fakebin)+os.pathsep+os.environ.get('PATH','/usr/bin:/bin')},capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    assert destination.read_bytes()==payload
    assert calls.read_text().splitlines()==['call','call']


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_TEST_NATIVE_APP'),reason='Built native preview fixture required')
def test_native_quit_policy_waits_for_drain_and_retries_after_failure():
    executable=Path(os.environ['SLEEP_IN_TEST_NATIVE_APP'])/'Contents/MacOS/SleepIn'
    result=subprocess.run([str(executable),'--self-test'],capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    evidence=json.loads(result.stdout)
    assert evidence['termination_decisions']==['ask','wait','ask','wait','exit']
    assert evidence['side_effects_performed'] is False


def test_fresh_worker_heartbeat_from_previous_generation_is_not_ready(tmp_path):
    from taskconsole.local import worker_health
    from taskconsole.store import Store,stamp
    store=Store(tmp_path,f'sqlite:///{tmp_path}/console.db')
    try:
        with store.transaction() as tx:tx.put('meta',{'id':'workflow_worker','status':'ready','last_seen':stamp(),'n8n_available':True,'instance_id':'previous'})
        assert worker_health(tmp_path,'current')=='unavailable'
        with store.transaction() as tx:tx.put('meta',{'id':'workflow_worker','status':'ready','last_seen':stamp(),'n8n_available':True,'instance_id':'current'})
        assert worker_health(tmp_path,'current')=='ready'
    finally:store.engine.dispose()
