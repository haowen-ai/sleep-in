"""Real n8n retries/cancellation with independent effect and process evidence."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime
import copy
import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
import subprocess
import threading
import time
from urllib.parse import urlparse

import pytest
from test_acceptance_data import live, configured_node
from test_acceptance_branches import native, mapping, run_graph, calls, admit
from test_acceptance_local_recovery import until, alive
from taskconsole.workflows_n8n import execute_graph, compile_graph, command
from taskconsole.workflows_execution import process_identity, terminate_orphan

pytestmark=pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='Actual n8n required')


def lines(path):return path.read_text().splitlines() if path.exists() else []


@contextmanager
def running(svc,run):
    thread=threading.Thread(target=execute_graph,args=(svc,run['id']),daemon=True);thread.start()
    try:yield thread
    finally:
        if thread.is_alive():svc.cancel(run['id']);thread.join(15)
        for state in svc.get_run(run['id'])['nodes'].values():
            if state.get('worker_pid'):
                terminate_orphan(state['worker_pid'],state.get('worker_identity'),svc.store.path/'workflow-runs'/run['id'])
        assert not thread.is_alive(),'Owned n8n adapter did not terminate'


def done(svc,run,thread):
    thread.join(25);assert not thread.is_alive()
    result=svc.get_run(run['id']);assert result.get('n8n_execution_id') and result.get('graph_execution_id'),result.get('adapter_log')
    return result


def test_default_one_attempt_effect_and_constant_only_descendant_blocked(live):
    ledger=live.store.path/'effect-ledger'
    a=native('a',body=f'with Path({str(ledger)!r}).open("a") as f: f.write("effect-001\\n")\nraise RuntimeError("after one effect")')
    b=native('b',inputs={'constant':{'source':'constant','value':7}},body='return inputs')
    run=run_graph(live,[a,b],[{'source':'a','target':'b'}],'Default one attempt')
    assert run['status']=='failed' and run['nodes']['a']['status']=='failed'
    assert lines(ledger)==['effect-001'];calls(live,run,'a',1);calls(live,run,'b',0)
    assert run['nodes']['b']['status']=='not_run' and run['nodes']['b']['reason']=='blocked_by_failed_dependency'


@pytest.mark.parametrize('failures',[1,2])
def test_explicit_retry_has_fresh_files_distinct_logs_and_one_consumer(live,failures):
    ledger=live.store.path/'retry-effects';consumer=live.store.path/'consumer-effects'
    source=f'''import os,json,sys
from pathlib import Path
attempt=int(Path(os.environ['SLEEP_IN_INPUT_FILE']).parent.name.split('-')[-1])
with Path({str(ledger)!r}).open('a') as f:f.write(str(attempt)+'\\n');f.flush();os.fsync(f.fileno())
print('stdout-attempt-'+str(attempt),flush=True)
print('stderr-attempt-'+str(attempt),file=sys.stderr,flush=True)
assert not Path(os.environ['SLEEP_IN_OUTPUT_FILE']).exists()
assert not Path(os.environ['SLEEP_IN_ARTIFACT_DIR'],'same.txt').exists()
Path(os.environ['SLEEP_IN_ARTIFACT_DIR'],'same.txt').write_text('attempt-'+str(attempt))
json.dump({{'schemaVersion':1,'data':{{'attempt':attempt}},'artifacts':[{{'name':'same.txt'}}]}},open(os.environ['SLEEP_IN_OUTPUT_FILE'],'w'))
if attempt<={failures}:raise RuntimeError('retry-safe transient '+str(attempt))
'''
    a={'id':'a','kind':'python','source':source,'config':{'entry_mode':'file','retry':{'max_attempts':3,'delay_seconds':.1,'safe_to_retry':True}},'inputs':{}}
    b=native('b',inputs={'attempt':mapping('a',path=['attempt']),'file':{'source':'artifact','node_id':'a','name':'same.txt'}},body=f'with Path({str(consumer)!r}).open("a") as f: f.write("consumer\\n")\nreturn {{"attempt":inputs["attempt"],"artifact":Path(inputs["file"]["path"]).read_text()}}')
    run=run_graph(live,[a,b],[{'source':'a','target':'b'}],'Retry immutable attempts')
    assert run['status']=='succeeded',run
    assert lines(ledger)==[str(n) for n in range(1,failures+2)] and lines(consumer)==['consumer']
    state=run['nodes']['a'];attempts=state['attempts']
    assert [a['status'] for a in attempts]==['failed']*failures+['succeeded']
    for index,attempt in enumerate(attempts,1):
        assert attempt['number']==index and f'stdout-attempt-{index}' in attempt['stdout'] and f'stderr-attempt-{index}' in attempt['stderr']
        assert datetime.fromisoformat(attempt['finished_at'])>=datetime.fromisoformat(attempt['started_at'])
        if index>1:assert (datetime.fromisoformat(attempt['started_at'])-datetime.fromisoformat(attempts[index-2]['finished_at'])).total_seconds()>=.1
        folder=live.store.path/'workflow-runs'/run['id']/'a'/f'attempt-{index}'
        assert json.loads((folder/'output.json').read_text())['data']=={'attempt':index}
        assert (folder/'artifacts'/'same.txt').read_text()==f'attempt-{index}'
    assert 'error' not in state and 'reason' not in state
    assert state['output']['data']=={'attempt':failures+1}
    assert len(run['artifacts'])==1 and f'/attempt-{failures+1}/' in run['artifacts'][0]['path']
    assert run['nodes']['b']['output']['data']=={'attempt':failures+1,'artifact':f'attempt-{failures+1}'}
    calls(live,run,'b',1)


def test_lost_authenticated_callback_response_replay_never_repeats_effect(live):
    ledger=live.store.path/'lost-response-effect';release=live.store.path/'finish-callback'
    a=native('a',body=f'import os,time\nwith Path({str(ledger)!r}).open("a") as f: f.write("effect-001\\n"); f.flush(); os.fsync(f.fileno())\nwhile not Path({str(release)!r}).exists(): time.sleep(.01)\nreturn {{"value":7}}')
    run=admit(live,[a],[],'Lost response');base=urlparse(str(live.acceptance_client.base_url));path=f'/internal/workflows/{run["id"]}/nodes/a'
    sock=socket.create_connection((base.hostname,base.port),timeout=5)
    body=b'{}';request=(f'POST {path} HTTP/1.1\r\nHost: {base.netloc}\r\nx-workflow-token: {run["callback_token"]}\r\nContent-Type: application/json\r\nContent-Length: 2\r\nConnection: close\r\n\r\n').encode()+body
    try:
        sock.sendall(request);until(lambda:lines(ledger)==['effect-001'],timeout=10)
        assert live.get_run(run['id'])['nodes']['a']['status']=='running'
        sock.setsockopt(socket.SOL_SOCKET,socket.SO_LINGER,struct.pack('ii',1,0));sock.close()
        release.touch();until(lambda:live.get_run(run['id'])['nodes']['a']['status']=='succeeded',timeout=10)
        before=copy.deepcopy(live.get_run(run['id'])['nodes']['a'])
        for _ in range(2):
            response=live.acceptance_client.post(path,headers={'x-workflow-token':run['callback_token']},json={})
            assert response.status_code==200 and response.json()['status']=='succeeded'
        execute_graph(live,run['id']);result=live.get_run(run['id'])
        assert result['status']=='succeeded' and result.get('n8n_execution_id')
        assert result['nodes']['a']==before and lines(ledger)==['effect-001']
        calls(live,result,'a',1)
    finally:release.touch();sock.close()


def test_explicit_full_rerun_pins_requested_version_and_fresh_effect_key(live):
    ledger=live.store.path/'manual-effects'
    source=f'from pathlib import Path\ndef main(inputs):\n with Path({str(ledger)!r}).open("a") as f: f.write(inputs["effect_key"]+":"+inputs["value"]+"\\n")\n if inputs["fail"]: raise RuntimeError("explicit first failure")\n return {{"version":"v1","value":inputs["value"]}}'
    node={'id':'a','kind':'python','source':source,'config':{},'inputs':{k:{'source':'parameter','path':[k]} for k in ['effect_key','value','fail']}}
    wf=live.save({'name':'Full rerun','nodes':[node],'edges':[]});pub=live.publish(wf['id']);prefix='/api/workflows/'+wf['id']+'/run'
    first=live.acceptance_client.post(prefix,json={'idempotency_key':'first','params':{'effect_key':'effect-001','value':'old','fail':True}});assert first.status_code==202
    execute_graph(live,first.json()['id']);original=live.get_run(first.json()['id']);assert original['status']=='failed'
    wf=live.save({**wf,'nodes':[{**node,'source':source.replace('"v1"','"v2"')}]},wf['id']);new=live.publish(wf['id']);assert new['version_id']!=pub['version_id']
    body={'idempotency_key':'explicit-new','version_id':pub['version_id'],'params':{'effect_key':'effect-002','value':'fresh','fail':False}}
    response=live.acceptance_client.post(prefix,json=body);assert response.status_code==202
    rid=response.json()['id'];assert rid!=original['id'];execute_graph(live,rid);rerun=live.get_run(rid)
    assert rerun['status']=='succeeded' and rerun['version_id']==original['version_id']==pub['version_id']
    assert rerun['params']==body['params'] and rerun['nodes']['a']['output']['data']=={'version':'v1','value':'fresh'}
    assert rerun.get('n8n_execution_id') and original.get('n8n_execution_id')
    assert lines(ledger)==['effect-001:old','effect-002:fresh']
    assert live.get_run(original['id'])==original
    assert live.acceptance_client.post(prefix,json=body).json()['id']==rid and lines(ledger)==['effect-001:old','effect-002:fresh']


def native_cli(svc,run):
    """Deliver an already queued/compiled graph even after its admission is cancelled."""
    directory=svc.store.path/'late-n8n'/run['id'];directory.mkdir(parents=True)
    graph=directory/'graph.json';graph.write_text(json.dumps(compile_graph(run,str(svc.acceptance_client.base_url))))
    env={k:v for k,v in os.environ.items() if not k.startswith(('N8N_','DB_','QUEUE_','EXECUTIONS_'))}
    with socket.socket() as sock:sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env.update(N8N_USER_FOLDER=str(directory),DB_TYPE='sqlite',N8N_ENCRYPTION_KEY=run['callback_token'],N8N_RUNNERS_ENABLED='false',N8N_DIAGNOSTICS_ENABLED='false',N8N_RUNNERS_BROKER_PORT=str(port),N8N_VERSION_NOTIFICATIONS_ENABLED='false',N8N_COMMUNITY_PACKAGES_ENABLED='false',N8N_TEMPLATES_ENABLED='false',N8N_ENFORCE_SETTINGS_FILE_PERMISSIONS='true')
    argv=command();env['PATH']=str(Path(argv[0]).parent)+os.pathsep+env.get('PATH','/usr/bin:/bin')
    for args in [['import:workflow','--input='+str(graph)],['execute','--id='+run['id'],'--rawOutput']]:
        result=subprocess.run(argv+args,cwd=directory,env=env,capture_output=True,text=True,timeout=70)
        assert result.returncode==0,(result.stdout[-3000:],result.stderr[-1000:])
    with sqlite3.connect('file:'+str(directory/'.n8n'/'database.sqlite')+'?mode=ro',uri=True) as db:
        assert db.execute('SELECT COUNT(*) FROM execution_entity WHERE workflowId=?',(run['id'],)).fetchone()[0]==1


def test_queued_cancel_then_actual_late_graph_and_callbacks_have_zero_effect(live):
    ledger=live.store.path/'forbidden-effect';a=native('a',body=f'Path({str(ledger)!r}).write_text("effect")\nreturn {{}}')
    run=admit(live,[a],[],'Queued cancellation');assert run['status']=='queued'
    response=live.acceptance_client.post('/api/workflow-runs/'+run['id']+'/cancel');assert response.status_code==200 and response.json()['status']=='cancelled'
    native_cli(live,run)
    terminal=live.get_run(run['id']);assert terminal['status']=='cancelled' and terminal.get('graph_execution_id')
    calls(live,terminal,'a',0);assert not ledger.exists()
    assert terminal['nodes']['a']['status']=='cancelled'


def child_node(svc,timeout=30,ignore=True):
    child_file=svc.store.path/'child-pid';ready=svc.store.path/'worker-ready';late=svc.store.path/'late-output'
    handshake=svc.store.path/'child-ready'
    child='import signal,time;from pathlib import Path;'+('signal.signal(signal.SIGTERM,signal.SIG_IGN);' if ignore else '')+f'Path({str(handshake)!r}).touch();time.sleep(60)'
    source=f'import subprocess,sys,signal,time\nfrom pathlib import Path\ndef main(inputs):\n signal.signal(signal.SIGTERM,signal.SIG_IGN)\n child=subprocess.Popen([sys.executable,"-c",{child!r}])\n Path({str(child_file)!r}).write_text(str(child.pid))\n while not Path({str(handshake)!r}).exists(): time.sleep(.01)\n Path({str(ready)!r}).write_text("ready")\n time.sleep(10)\n Path({str(late)!r}).write_text("must-not-arrive")\n return {{"late":True}}'
    return {'id':'slow','kind':'python','source':source,'config':{'timeout':timeout},'inputs':{}},child_file,ready,late


def test_cancel_live_child_and_completed_sibling_never_runs_join(live):
    slow,child_file,ready,late=child_node(live)
    fast=native('fast',{'done':True});join=native('join',inputs={'a':mapping('slow'),'b':mapping('fast')},config={'join':'all','merge':'named'})
    run=admit(live,[slow,fast,join],[{'source':n,'target':'join'} for n in ['slow','fast']],'Cancel live branch')
    with running(live,run) as thread:
        until(lambda:ready.exists() and live.get_run(run['id'])['nodes']['fast']['status']=='succeeded',timeout=70)
        before=live.get_run(run['id']);pid=before['nodes']['slow']['worker_pid'];child=int(child_file.read_text());assert alive(pid) and alive(child)
        response=live.acceptance_client.post('/api/workflow-runs/'+run['id']+'/cancel');assert response.status_code==200 and response.json()['status']=='cancelling'
        assert alive(pid) and alive(child)
        terminal=done(live,run,thread);assert terminal['status']=='cancelled'
        assert not alive(pid) and not alive(child) and not late.exists()
        assert terminal['nodes']['fast']['status']=='succeeded' and terminal['nodes']['slow']['status']=='cancelled'
        calls(live,terminal,'fast',1);calls(live,terminal,'join',0)
        assert terminal['nodes']['join']['status']=='cancelled'


def test_node_one_second_timeout_stops_process_tree_and_blocks_late_output(live):
    slow,child_file,ready,late=child_node(live,timeout=1)
    never=native('never',body='raise RuntimeError("forbidden")')
    run=admit(live,[slow,never],[{'source':'slow','target':'never'}],'Node deadline')
    with running(live,run) as thread:
        until(lambda:ready.exists(),timeout=70);pid=live.get_run(run['id'])['nodes']['slow']['worker_pid'];child=int(child_file.read_text())
        terminal=done(live,run,thread)
        assert terminal['status']=='timed_out' and terminal['nodes']['slow']['status']=='timed_out'
        assert not alive(pid) and not alive(child) and not late.exists()
        assert 'output' not in terminal['nodes']['slow'];calls(live,terminal,'never',0)
        assert terminal['nodes']['never']['status']=='not_run'


@pytest.mark.parametrize('iteration',[0,1,2])
def test_cancel_races_complete_output_barrier_without_success_or_duplicate(live,iteration):
    ready=live.store.path/'ready';release=live.store.path/'release';written=live.store.path/'written';ledger=live.store.path/'effects'
    source=f'''import os,json,time,signal
from pathlib import Path
signal.signal(signal.SIGTERM,signal.SIG_IGN)
with Path({str(ledger)!r}).open('a') as f:f.write('effect-001\\n');f.flush();os.fsync(f.fileno())
Path({str(ready)!r}).touch()
while not Path({str(release)!r}).exists():time.sleep(.005)
json.dump({{'schemaVersion':1,'data':{{'value':7}},'artifacts':[]}},open(os.environ['SLEEP_IN_OUTPUT_FILE'],'w'))
Path({str(written)!r}).touch()
time.sleep({[0,.003,10][iteration]})
'''
    a={'id':'a','kind':'python','source':source,'config':{'entry_mode':'file'},'inputs':{}}
    run=admit(live,[a],[],'Output cancel race')
    with running(live,run) as thread:
        until(lambda:ready.exists(),timeout=70);pid=live.get_run(run['id'])['nodes']['a']['worker_pid'];barrier=threading.Barrier(2)
        def cancel():barrier.wait();return live.acceptance_client.post('/api/workflow-runs/'+run['id']+'/cancel')
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(cancel);barrier.wait();release.touch();response=pending.result(10)
        assert response.status_code==200 and response.json()['status'] in {'cancelling','cancelled','succeeded'}
        until(lambda:written.exists(),timeout=2)
        output=live.store.path/'workflow-runs'/run['id']/'a'/'attempt-1'/'output.json'
        assert json.loads(output.read_text())['data']=={'value':7}
        terminal=done(live,run,thread);assert not alive(pid)
        assert terminal['status']==('succeeded' if response.json()['status']=='succeeded' else 'cancelled')
        state=terminal['nodes']['a']
        assert state['status'] in {'succeeded','cancelled'}
        # A just-completed worker may retain diagnostic output when cancel wins.
        # The contract forbids terminal reversal or duplication, not retaining evidence.
        if 'output' in state:assert state['output']=={'schemaVersion':1,'data':{'value':7},'artifacts':[]}
        if state['status']=='succeeded':assert 'output' in state
        if response.json()['status']=='cancelling':assert state['status']=='cancelled'
        assert len(state['attempts'])==1 and lines(ledger)==['effect-001'] and terminal['artifacts']==[]
        for _ in range(2):
            replay=live.acceptance_client.post(f'/internal/workflows/{run["id"]}/nodes/a',headers={'x-workflow-token':run['callback_token']},json={})
            assert replay.status_code==200 and replay.json()['status']==state['status']
        assert live.get_run(run['id'])==terminal and lines(ledger)==['effect-001']


@pytest.mark.parametrize('closed_stdio',[False,True])
def test_cancel_stops_ignoring_child_even_when_parent_exits_on_term(live,closed_stdio):
    slow,child_file,ready,late=child_node(live)
    slow['source']=slow['source'].replace(' signal.signal(signal.SIGTERM,signal.SIG_IGN)\n','')
    if closed_stdio:slow['source']=slow['source'].replace('])\n Path(','],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n Path(',1)
    never=native('never',body='raise RuntimeError("forbidden")')
    run=admit(live,[slow,never],[{'source':'slow','target':'never'}],'Parent exits child resists')
    child=pid=None
    try:
        with running(live,run) as thread:
            until(lambda:ready.exists(),timeout=70)
            pid=live.get_run(run['id'])['nodes']['slow']['worker_pid'];child=int(child_file.read_text())
            assert alive(pid) and alive(child) and os.getpgid(child)==pid
            response=live.acceptance_client.post('/api/workflow-runs/'+run['id']+'/cancel');assert response.status_code==200
            terminal=done(live,run,thread)
            assert terminal['status']=='cancelled' and not alive(pid)
            assert not alive(child),'Cancellation reported terminal while owned SIGTERM-resistant child remained alive'
            calls(live,terminal,'never',0);assert not late.exists()
    finally:
        if child and alive(child) and os.getpgid(child)==pid:
            # This group is the observed isolated worker's group, still held by its child.
            import signal
            os.killpg(pid,signal.SIGKILL)
            until(lambda:not alive(child),timeout=5)
