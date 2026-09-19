"""Actual engine startup and live process trees share the unshifted workflow budget."""
from datetime import datetime
import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time

import pytest
from test_acceptance_data import live, configured_node
from test_acceptance_branches import native, calls
from test_acceptance_gold_concurrency import publish
from test_acceptance_local_recovery import alive, until
from test_acceptance_retry_cancel import running
from taskconsole.workflows_n8n import command, execute_graph

pytestmark=pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='Actual pinned n8n required')


def admit_graph(svc,nodes,budget):
    graph={'name':f'Whole graph budget {budget}s','timeout':budget,'nodes':nodes,'edges':[{'source':a['id'],'target':b['id']} for a,b in zip(nodes,nodes[1:])]}
    wf,_=publish(svc,graph)
    response=svc.acceptance_client.post('/api/workflows/'+wf['id']+'/run',json={});assert response.status_code==202,response.text
    return svc.get_run(response.json()['id'])


def deadline_seconds(run):
    return (datetime.fromisoformat(run['finished_at'])-datetime.fromisoformat(run['started_at'])).total_seconds()


def reject_late_success(svc,run):
    headers={'x-workflow-token':run['callback_token']};prefix='/internal/workflows/'+run['id']
    for node in run['nodes']:
        for suffix in ['', '/submit']:
            response=svc.acceptance_client.post(prefix+'/nodes/'+node+suffix,headers=headers,json={'retry':True,'output':{'schemaVersion':1,'data':{'late':True},'artifacts':[]}})
            assert response.status_code in {200,202},response.text
    response=svc.acceptance_client.post(prefix+'/finish',headers=headers,json={'status':'succeeded'});assert response.status_code==200,response.text
    assert svc.get_run(run['id'])==run


def test_actual_n8n_two_second_budget_includes_import_without_shift_or_hidden_grace(live,monkeypatch,record_property):
    # A longer startup allowance, reset timer, or late callback admission must fail this oracle.
    ledger=live.store.path/'worker-starts'
    body=f'import json,os,time\nwith Path({str(ledger)!r}).open("a") as f:f.write(json.dumps({{"node":"slow","at":time.time(),"pid":os.getpid()}})+"\\n");f.flush();os.fsync(f.fileno())\ntime.sleep(120)\nreturn {{"forbidden":True}}'
    slow=native('slow',body=body,config={'timeout':120})
    nodes=[slow,native('never',{'forbidden':True},config={'timeout':120}),native('last',{'forbidden':True},config={'timeout':120})]
    run=admit_graph(live,nodes,2);launches=[];real_popen=subprocess.Popen;actual_command=command()
    def observe(argv,*args,**kwargs):
        process=real_popen(argv,*args,**kwargs)
        if list(argv[:len(actual_command)])==actual_command:launches.append((list(argv),process))
        return process
    monkeypatch.setattr(subprocess,'Popen',observe)
    start=time.monotonic();execute_graph(live,run['id']);elapsed=time.monotonic()-start
    # Cleanup has a bounded TERM/KILL interval; it never grants more workflow work time.
    until(lambda:all(state['status'] not in {'running','dispatching'} for state in live.get_run(run['id'])['nodes'].values()),timeout=4)
    result=live.get_run(run['id']);elapsed_total=time.monotonic()-start
    assert launches and any('import:workflow' in argv for argv,_ in launches)
    assert all(process.poll() is not None for _,process in launches)
    assert result['status']=='timed_out' and result['error']=='Workflow deadline exceeded'
    logical=deadline_seconds(result)
    assert 2<=logical<3 and elapsed<5.5 and elapsed_total<6
    assert all(node['config']['timeout']>result['timeout'] for node in result['snapshot']['nodes'])
    cutoff=datetime.fromisoformat(result['started_at']).timestamp()+2
    starts=[json.loads(line) for line in ledger.read_text().splitlines()] if ledger.exists() else []
    assert len(starts)<=1 and all(row['at']<=cutoff for row in starts)
    for nid,state in result['nodes'].items():
        assert state['status'] in {'timed_out','not_run'} and state.get('output') is None and not state.get('worker_pid')
        if state.get('started_at'):assert datetime.fromisoformat(state['started_at']).timestamp()<=cutoff
        if nid!='slow':calls(live,result,nid,0)
    assert result['artifacts']==[] and result['adapter_pid'] is None and result['adapter_finished_at']
    record_property('workflow_budget_seconds',2);record_property('logical_terminal_seconds',round(logical,3))
    record_property('adapter_elapsed_seconds',round(elapsed,3));record_property('cleanup_elapsed_seconds',round(elapsed_total,3))
    record_property('actual_engine_actions',[argv[len(actual_command)] for argv,_ in launches])
    record_property('actual_worker_count',len(starts));record_property('deadline_phase','worker' if starts else 'engine_startup_no_worker')
    reject_late_success(live,result)


def test_actual_whole_graph_deadline_kills_live_worker_child_and_blocks_downstream(live,record_property):
    budget=30;ready=live.store.path/'worker-ready';child_ready=live.store.path/'child-ready';release=live.store.path/'release-after-deadline';late=live.store.path/'late-output';effects=live.store.path/'started-effects'
    child=f'import os,signal,time;from pathlib import Path;signal.signal(signal.SIGTERM,signal.SIG_IGN);Path({str(child_ready)!r}).write_text(str(os.getpid()));time.sleep(120)'
    body=f'''import os,sys,subprocess,signal,json,time
signal.signal(signal.SIGTERM,signal.SIG_IGN)
child=subprocess.Popen([sys.executable,"-c",{child!r}],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
while not Path({str(child_ready)!r}).exists():time.sleep(.01)
with Path({str(effects)!r}).open("a") as f:f.write("effect-001\\n");f.flush();os.fsync(f.fileno())
Path({str(ready)!r}).write_text(json.dumps({{"worker":os.getpid(),"child":child.pid,"at":time.time()}}))
while not Path({str(release)!r}).exists():time.sleep(.01)
Path({str(late)!r}).write_text("forbidden late output")
return {{"late":True}}
'''
    nodes=[native('slow',body=body,config={'timeout':120}),native('never',{'late':True},config={'timeout':120}),native('last',{'late':True},config={'timeout':120})]
    run=admit_graph(live,nodes,budget);pid=child_pid=None;child_identity=None;started=time.monotonic()
    try:
        with running(live,run) as thread:
            until(lambda:ready.exists() and ready.stat().st_size,timeout=budget-2)
            started_record=json.loads(ready.read_text());pid=started_record['worker'];child_pid=started_record['child']
            before=live.get_run(run['id']);cutoff=datetime.fromisoformat(before['started_at']).timestamp()+budget
            assert before['status']=='running' and before.get('graph_execution_id')
            assert before['nodes']['slow']['worker_pid']==pid and alive(pid) and alive(child_pid)
            assert os.getpgid(child_pid)==pid and started_record['at']<cutoff
            child_identity=subprocess.check_output(['/bin/ps','-p',str(child_pid),'-o','lstart=','-o','pgid='],text=True).strip()
            assert all(node['config']['timeout']>budget for node in before['snapshot']['nodes'])
            terminal_at=until(lambda:(r if (r:=live.get_run(run['id']))['status']=='timed_out' else None),timeout=budget+3)
            logical=deadline_seconds(terminal_at);assert budget<=logical<budget+1
            thread.join(6);assert not thread.is_alive()
            until(lambda:not alive(pid) and not alive(child_pid) and live.get_run(run['id'])['nodes']['slow']['status']=='timed_out',timeout=4)
            terminal=live.get_run(run['id']);elapsed=time.monotonic()-started
            assert elapsed<budget+6 and terminal['status']=='timed_out'
            assert terminal['error']=='Workflow deadline exceeded' and terminal['nodes']['slow']['error']=='Workflow deadline exceeded'
            assert terminal['nodes']['slow']['attempts'][0]['status']=='timed_out'
            assert terminal['nodes']['slow'].get('output') is None and terminal['artifacts']==[]
            assert effects.read_text().splitlines()==['effect-001'] and not late.exists()
            calls(live,terminal,'slow',1)
            for nid in ['never','last']:
                calls(live,terminal,nid,0)
                assert terminal['nodes'][nid]['status']=='not_run' and terminal['nodes'][nid]['reason']=='workflow_timed_out'
            assert terminal.get('n8n_execution_id') and terminal.get('graph_execution_id') and terminal['adapter_pid'] is None
            release.touch();reject_late_success(live,terminal)
            assert not late.exists() and effects.read_text().splitlines()==['effect-001'] and not alive(pid) and not alive(child_pid)
            record_property('workflow_budget_seconds',budget);record_property('logical_terminal_seconds',round(logical,3))
            record_property('worker_started_after_orchestration_seconds',round(started_record['at']-datetime.fromisoformat(before['started_at']).timestamp(),3))
            record_property('all_processes_stopped_elapsed_seconds',round(elapsed,3));record_property('local_node_timeout_seconds',120)
    finally:
        release.touch()
        # Clean up only an exact observed child start/group identity if an assertion fails.
        if child_pid and alive(child_pid) and child_identity:
            current=subprocess.run(['/bin/ps','-p',str(child_pid),'-o','lstart=','-o','pgid='],capture_output=True,text=True).stdout.strip()
            if current==child_identity and os.getpgid(child_pid)==pid:
                try:os.killpg(pid,signal.SIGKILL)
                except ProcessLookupError:pass
