"""Isolated AppKit controller tests. No login items, foreground UI or power changes.

The harness compiles the production delegate unchanged, excluding only its app
entrypoint. Only OS modal responses/registration and foreground presentation are
replaced; alert widgets, controller actions, progress rendering and subprocess
commands remain production code. Artifacts describe software evidence only.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import textwrap

import pytest

ROOT=Path(__file__).resolve().parents[1]
pytestmark=pytest.mark.skipif(sys.platform!='darwin',reason='BLOCKED_ENV: AppKit compiler/runtime requires macOS')

HARNESS=r'''
final class AcceptanceDelegate: SleepInDelegate {
    let fixtureRoot:URL
    let fixtureLauncher:URL
    var choices:[NSApplication.ModalResponse]=[]
    var dialogs:[[String:Any]]=[]
    init(root:URL,launcher:URL) {fixtureRoot=root;fixtureLauncher=launcher;super.init()}
    override var root:URL {fixtureRoot}
    override var launcher:URL {fixtureLauncher}
    override func refreshRegistration() {knownEnabled=false}
    override func presentAlert(_ alert:NSAlert)->NSApplication.ModalResponse {
        dialogs.append(["message":alert.messageText,"buttons":alert.buttons.map{$0.title},"detail":alert.informativeText])
        return choices.isEmpty ? .alertThirdButtonReturn : choices.removeFirst()
    }
    override func showProgress(present:Bool=true) {super.showProgress(present:false)}
}
// Force this fixture's language in memory only; never write host preferences.
UserDefaults.standard.setVolatileDomain(["locale":"en"],forName:UserDefaults.argumentDomain)
let app=NSApplication.shared
app.setActivationPolicy(.prohibited)
let root=URL(fileURLWithPath:CommandLine.arguments[1])
let scenario=CommandLine.arguments[2]
let delegate=AcceptanceDelegate(root:root,launcher:root.appendingPathComponent("launcher"))
delegate.statusItem=NSMenuItem(title:"Background running",action:nil,keyEquivalent:"")
delegate.loginItem=NSMenuItem();delegate.powerItem=NSMenuItem();delegate.nextItem=NSMenuItem()
func waitUntil(_ condition:()->Bool) {
    let end=Date().addingTimeInterval(30)
    while !condition() && Date()<end {RunLoop.current.run(until:Date().addingTimeInterval(0.02))}
    if !condition() {fputs("Native fixture timed out\n",stderr);exit(2)}
}
var result:[String:Any]=[:]
switch scenario {
case "keep", "finish", "cancel":
    delegate.choices=[scenario == "finish" ? .alertFirstButtonReturn : scenario == "cancel" ? .alertSecondButtonReturn : .alertThirdButtonReturn]
    delegate.stopService();waitUntil{!delegate.busy && delegate.statusItem.title != "Preparing background service…"}
    RunLoop.current.run(until:Date().addingTimeInterval(0.1))
    result=["dialogs":delegate.dialogs,"state_label":delegate.statusItem.title,"busy":delegate.busy]
case "failure-retry":
    delegate.choices=[.alertFirstButtonReturn]
    delegate.launch("--launch")
    waitUntil{!delegate.busy && delegate.dialogs.count==1 && delegate.statusItem.title != "Preparing background service…"}
    RunLoop.current.run(until:Date().addingTimeInterval(0.1))
    result=["dialogs":delegate.dialogs,"state_label":delegate.statusItem.title,"busy":delegate.busy]
case "failure-details":
    delegate.choices=[.alertSecondButtonReturn]
    delegate.launch("--launch")
    waitUntil{!delegate.busy && delegate.detailsVisible}
    result=["dialogs":delegate.dialogs,"state_label":delegate.statusItem.title,"details":delegate.progressText?.string ?? "","busy":delegate.busy]
case "progress":
    delegate.busy=true;delegate.showProgress(present:false);delegate.refreshProgress()
    result=["progress_label":delegate.progressLabel?.stringValue ?? "","percent":delegate.progressBar?.doubleValue ?? -1,"indeterminate":delegate.progressBar?.isIndeterminate ?? true,"visible":delegate.progressWindow?.isVisible ?? true]
case "large-status":
    let response=delegate.command("--status",capture:true)
    result=["exit":response.0,"bytes":response.1.utf8.count]
default:exit(3)
}
let data=try! JSONSerialization.data(withJSONObject:result,options:[.sortedKeys])
print(String(data:data,encoding:.utf8)!)
delegate.progressTimer?.invalidate();delegate.progressWindow?.close()
'''


@pytest.fixture(scope='session')
def native_controller(tmp_path_factory):
    directory=tmp_path_factory.mktemp('native-controller')
    source=(ROOT/'packaging/SleepIn.swift').read_text()
    # The real application entrypoint starts services and may offer permissions.
    # Compile only production declarations plus the isolated test entrypoint.
    declarations,separator,_=source.partition('if CommandLine.arguments.contains("--self-test")')
    assert separator,'Native application entrypoint must be explicitly identified before safe compilation'
    swift=directory/'main.swift';swift.write_text(declarations+HARNESS)
    executable=directory/'native-controls'
    built=subprocess.run(['/usr/bin/swiftc','-framework','AppKit','-framework','ServiceManagement',str(swift),'-o',str(executable)],capture_output=True,text=True,timeout=120)
    assert built.returncode==0,built.stderr
    return executable


def run_native(executable,root,scenario,timeout=40):
    process=subprocess.Popen([str(executable),str(root),scenario],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True,cwd=ROOT)
    try:
        output,error=process.communicate(timeout=timeout)
        assert process.returncode==0,error
        return json.loads(output)
    finally:
        if process.poll() is None:
            os.killpg(process.pid,signal.SIGKILL);process.wait()


def launcher(root,body):
    root.mkdir(parents=True,exist_ok=True)
    script=root/'launcher';script.write_text('#!/bin/bash\nset -euo pipefail\n'+body);script.chmod(0o700)


def test_native_large_status_response_cannot_deadlock_capture(native_controller,tmp_path):
    import shlex
    launcher(tmp_path,'exec '+shlex.quote(sys.executable)+' -c '+shlex.quote('print("x"*262144)')+'\n')
    result=run_native(native_controller,tmp_path,'large-status',timeout=8)
    assert result['exit']==0 and result['bytes']==262145


def test_mac_bg11_native_keep_running_dispatches_no_stop_or_preference_change(native_controller,tmp_path):
    calls=tmp_path/'commands'
    launcher(tmp_path,'echo "$1" >> '+repr(str(calls))+'\nexit 99\n')
    state=tmp_path/'state';state.mkdir();pref=state/'local-preferences.json';pref.write_text('{"running":true,"fixture":"preserve"}')
    before=pref.read_bytes()
    result=run_native(native_controller,tmp_path,'keep')
    assert not calls.exists()
    assert pref.read_bytes()==before
    assert result['state_label']=='Background running' and not result['busy']
    assert result['dialogs'][0]['buttons']==['Finish active work','Cancel active work','Keep running']


def test_mac_in04_native_partial_download_progress_uses_private_actual_bytes(native_controller,tmp_path):
    launcher(tmp_path,'exit 99\n')
    downloads=tmp_path/'downloads';downloads.mkdir();(downloads/'uv.tar.gz.partial').write_bytes(b'x'*2048)
    (tmp_path/'install-progress.json').write_text(json.dumps({'phase':'downloading','download_name':'uv.tar.gz.partial','total_bytes':8192}))
    result=run_native(native_controller,tmp_path,'progress')
    assert result['percent']==25 and not result['indeterminate']
    assert '2' in result['progress_label'] and '8' in result['progress_label']
    assert result['visible'] is False


def test_mac_in06_failed_worker_shows_details_instead_of_ready(native_controller,tmp_path):
    launcher(tmp_path,'echo "Worker readiness failed: synthetic missing engine capability" >&2\nexit 1\n')
    result=run_native(native_controller,tmp_path,'failure-details')
    assert result['state_label']=='Background needs attention'
    assert result['dialogs'][0]['buttons']==['Retry','View details','Close']
    assert 'Worker readiness failed' in result['details']
    assert not result['busy']


def test_mac_in03_native_retry_reuses_action_and_waits_for_success(native_controller,tmp_path):
    import shlex
    calls=tmp_path/'commands'
    launcher(tmp_path,textwrap.dedent(f'''\
        echo "$1" >> {shlex.quote(str(calls))}
        case "$1" in
        --launch)
          if [ ! -f {shlex.quote(str(tmp_path/'retried'))} ]; then touch {shlex.quote(str(tmp_path/'retried'))}; echo 'Address already in use' >&2; exit 1; fi
          ;;
        --status) echo '{{"state":"running","running_preference":true,"power":{{"source":"unknown"}}}}' ;;
        esac
    '''))
    result=run_native(native_controller,tmp_path,'failure-retry')
    assert calls.read_text().splitlines()==['--launch','--launch','--status']
    assert result['state_label']=='Background running'
    assert len(result['dialogs'])==1
    assert 'Address already in use' in (tmp_path/'native-launch.txt').read_text()


# These are real app/worker/n8n effects, combined with the native NSAlert/controller
# fixture above. OS modal selection is synthetic; assertion hardware is disabled.
from test_acceptance_local_recovery import supervisor,until,publish,finished,healthy_same,alive
from taskconsole.local import status,read_json

requires_engine=pytest.mark.skipif(not os.environ.get('SLEEP_IN_TEST_N8N_COMMAND'),reason='BLOCKED_ENV: real Node/n8n fixture required')


def bridge_launcher(s):
    import shlex
    command=' '.join(map(shlex.quote,[sys.executable,'-m','taskconsole.local','--state',str(s.state)]))
    calls=s.state.parent/'native-commands'
    launcher(s.state.parent,textwrap.dedent(f'''\
        echo "$1" >> {shlex.quote(str(calls))}
        case "$1" in
          --status) exec {command} status ;;
          --stop-finish) exec {command} stop --mode finish ;;
          --stop-cancel) exec {command} stop --mode cancel ;;
          *) echo 'Unexpected command' >&2; exit 99 ;;
        esac
    '''))
    return calls


def waiting_workflow(s,label):
    started=s.state/(label+'-started');release=s.state/(label+'-release')
    source=f'''from pathlib import Path
import time

def main(inputs):
    Path({str(started)!r}).write_text('started')
    while not Path({str(release)!r}).exists(): time.sleep(.1)
    return {{"finished": True}}
'''
    return publish(s,source),started,release


@requires_engine
def test_mac_bg11_native_dismiss_keeps_real_active_run_and_service(native_controller,supervisor):
    s=supervisor;calls=bridge_launcher(s)
    wf,started,release=waiting_workflow(s,'keep')
    run=s.svc.admit(wf['id']);until(started.exists)
    before=(s.state/'local-preferences.json').read_bytes()
    result=run_native(native_controller,s.state.parent,'keep')
    assert not calls.exists() and not (s.state/'local-stop-request.json').exists()
    assert (s.state/'local-preferences.json').read_bytes()==before
    assert s.svc.get_run(run['id'])['status']=='running'
    assert result['state_label']=='Background running'
    healthy_same(s)
    release.write_text('finish')
    assert finished(s,run['id'])['status']=='succeeded'


@requires_engine
def test_mac_bg09_native_finish_drains_active_and_queued_runs_once(native_controller,supervisor):
    s=supervisor;calls=bridge_launcher(s)
    one,started_one,release_one=waiting_workflow(s,'first')
    two,started_two,release_two=waiting_workflow(s,'second')
    first=s.svc.admit(one['id']);second=s.svc.admit(two['id'])
    until(lambda:started_one.exists() and started_two.exists())
    queued=s.svc.admit(s.template['id'])
    assert s.svc.get_run(queued['id'])['status']=='queued'
    result=run_native(native_controller,s.state.parent,'finish')
    assert calls.read_text().splitlines()==['--stop-finish','--status']
    assert result['state_label']=='Background: draining'
    assert status(s.state)['state']=='draining' and alive(s.initial['pid'])
    assert read_json(s.state/'local-preferences.json')['running'] is False
    assert s.client.post('/api/workflows/'+s.template['id']+'/run',json={}).status_code==409
    release_one.write_text('finish');release_two.write_text('finish')
    until(lambda:status(s.state)['state']=='stopped',timeout=80)
    for admitted in (first,second,queued):
        run=s.svc.get_run(admitted['id']);assert run['status']=='succeeded' and run['adapter_finished_at']
        assert all(len(node['attempts'])==1 for node in run['nodes'].values())
    assert all(not alive(pid) for pid in s.initial['children'].values())


@requires_engine
def test_mac_bg10_native_cancel_waits_for_process_ack_and_blocks_downstream(native_controller,supervisor):
    s=supervisor;calls=bridge_launcher(s)
    marker=s.state/'effect-started';downstream=s.state/'unexpected-downstream'
    source=f'''import os,time
from pathlib import Path

def main(inputs):
    Path({str(marker)!r}).write_text(str(os.getpid()))
    while True:time.sleep(.1)
'''
    wf=s.svc.save({'name':'Native cancellation ledger','timeout':180,'nodes':[
        {'id':'source','kind':'python','source':source,'inputs':{},'config':{}},
        {'id':'later','kind':'python','source':f'from pathlib import Path\ndef main(inputs):\n Path({str(downstream)!r}).write_text("unexpected")\n return {{}}','inputs':{},'config':{}}],
        'edges':[{'source':'source','target':'later'}]})
    s.svc.publish(wf['id']);run=s.svc.admit(wf['id']);until(marker.exists);pid=int(marker.read_text())
    result=run_native(native_controller,s.state.parent,'cancel')
    assert calls.read_text().splitlines()==['--stop-cancel','--status']
    # A very fast cancellation may complete before the status subprocess samples.
    assert result['state_label'] in {'Background: draining','Background: stopped'}
    if result['state_label']=='Background: stopped':assert not alive(pid)
    until(lambda:status(s.state)['state']=='stopped',timeout=50)
    ended=s.svc.get_run(run['id'])
    assert ended['status']=='cancelled' and ended['adapter_finished_at']
    assert ended['nodes']['source']['status']=='cancelled'
    assert ended['nodes']['later']['status'] in {'cancelled','skipped','not_run'}
    assert not alive(pid) and not downstream.exists()
    assert all(not alive(child) for child in s.initial['children'].values())
