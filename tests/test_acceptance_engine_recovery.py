"""Owned-process interruption after fsynced effects; genuine supervisor and n8n."""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import time

import pytest
from test_acceptance_local_recovery import supervisor, until, alive, finished
from taskconsole.local import status, request_stop
from taskconsole.workflows_execution import process_identity, terminate_orphan

pytestmark=pytest.mark.skipif(not os.environ.get('SLEEP_IN_TEST_N8N_COMMAND'),reason='BLOCKED_ENV: actual pinned supervisor n8n required')


def execution_count(s,rid):
    path=s.state/'workflow-runs'/rid/'n8n'/'.n8n'/'database.sqlite'
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
        return db.execute('SELECT COUNT(*) FROM execution_entity WHERE workflowId=?',(rid,)).fetchone()[0]


def app_child(s):
    wrapper=status(s.state)['children']['app']
    values=subprocess.check_output(['/usr/bin/pgrep','-P',str(wrapper)],text=True).split()
    assert len(values)==1,values
    pid=int(values[0]);command=subprocess.check_output(['/bin/ps','-p',str(pid),'-o','command='],text=True)
    assert 'taskconsole' in command and 'serve' in command
    return pid


@pytest.mark.parametrize('component',['n8n','app','worker'])
def test_owned_engine_component_death_preserves_effect_and_future_schedule(supervisor,component):
    s=supervisor;ledger=s.state/'external-business-effects';child_file=s.state/'business-child-pid';release=s.state/'release-business'
    source=('import os,sys,time,subprocess\nfrom pathlib import Path\ndef main(inputs):\n'
        ' if inputs["mode"]=="crash":\n'
        '  child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(180)"])\n'
        f'  Path({str(child_file)!r}).write_text(str(child.pid))\n'
        f' with Path({str(ledger)!r}).open("a") as f:\n'
        '  f.write(inputs["mode"]+"\\n");f.flush();os.fsync(f.fileno())\n'
        ' if inputs["mode"]=="crash":\n'
        f'  while not Path({str(release)!r}).exists(): time.sleep(.05)\n'
        ' return {"mode":inputs["mode"]}\n')
    graph={'name':'Crash '+component,'timeout':180,'params':{'mode':'crash'},'nodes':[{'id':'effect','kind':'python','source':source,'inputs':{'mode':{'source':'parameter','path':['mode']}},'config':{}}],'edges':[]}
    response=s.client.post('/api/workflows',json=graph);assert response.status_code==200,response.text
    wf=response.json();prefix='/api/workflows/'+wf['id'];assert s.client.post(prefix+'/publish').status_code==200
    response=s.client.post(prefix+'/run',json={'idempotency_key':'uncertain-effect'});assert response.status_code==202
    rid=response.json()['id'];owned=[]
    try:
        running=until(lambda:(r if (r:=s.svc.get_run(rid)).get('adapter_pid') and r['nodes']['effect'].get('worker_pid') and child_file.exists() and ledger.exists() else None))
        assert ledger.read_text().splitlines()==['crash']
        worker_pid=running['nodes']['effect']['worker_pid'];adapter_pid=running['adapter_pid'];child_pid=int(child_file.read_text())
        for pid in [worker_pid,adapter_pid]:owned.append((pid,process_identity(pid)))
        assert all(alive(pid) for pid in [worker_pid,adapter_pid,child_pid])
        assert running['nodes']['effect']['status']=='running' and len(running['nodes']['effect']['attempts'])==1
        assert running.get('graph_execution_id') and execution_count(s,rid)==1
        before=status(s.state)
        if component=='n8n':victim=adapter_pid
        elif component=='app':victim=app_child(s)
        else:
            with s.store.transaction() as tx:victim=tx.get('meta','workflow_worker')['pid']
            assert victim not in {adapter_pid,worker_pid}
        os.kill(victim,signal.SIGKILL)
        until(lambda:not alive(victim),timeout=15)
        if component=='n8n':
            terminal=finished(s,rid)
            assert terminal['status']=='failed'
            # Installed CLI/healthy coordinator may remain ready while this graph failed.
            public=s.client.get('/api/workflow-runs/'+rid);assert public.status_code==200 and public.json()['status']=='failed'
            request_stop(s.state,'cancel')
        else:
            until(lambda:status(s.state)['state'] in {'degraded','stopped'},timeout=15)
        until(lambda:status(s.state)['state']=='stopped',timeout=40)
        replacement=s.launch('--explicit') if component=='n8n' else s.launch()
        assert replacement['generation']!=before['generation'] and replacement['components']['power']=='disabled-for-test'
        assert s.launch()['pid']==replacement['pid']
        try:until(lambda:all(not alive(pid) for pid in [worker_pid,adapter_pid,child_pid]),timeout=20)
        except AssertionError:
            raise AssertionError({'alive':{label:alive(pid) for label,pid in [('business',worker_pid),('n8n',adapter_pid),('business_child',child_pid)]},'run':s.svc.get_run(rid)})
        terminal=s.svc.get_run(rid)
        assert terminal['status']=='failed' and terminal.get('adapter_finished_at')
        assert any(word in (terminal.get('error') or '').lower() for word in ['interrupted','uncertain']),terminal.get('error')
        assert terminal['nodes']['effect']['status'] not in {'running','queued','dispatching'}
        assert len(terminal['nodes']['effect']['attempts'])==1
        assert ledger.read_text().splitlines()==['crash']
        assert terminal['graph_execution_id']==running['graph_execution_id'] and terminal['adapter_lease']==running['adapter_lease']
        assert execution_count(s,rid)==1
        assert s.svc.admit(wf['id'],key='uncertain-effect')['id']==rid
        # A future scheduled instant is supplied to the scheduler, without changing the host clock.
        due=(datetime.now(timezone.utc)+timedelta(days=1)).replace(second=0,microsecond=0)
        wf=s.svc.save({**wf,'triggers':[{'id':'later','kind':'scheduled','enabled':True,'params':{'mode':'future'},'schedule':{'kind':'once','date':due.date().isoformat(),'time':due.strftime('%H:%M')},'timezone':'UTC'}]},wf['id'])
        s.svc.tick(due-timedelta(seconds=1));scheduled=s.svc.tick(due)
        assert len(scheduled)==1 and scheduled[0]['trigger_id']=='later'
        future=finished(s,scheduled[0]['id'])
        assert future['status']=='succeeded' and future.get('n8n_execution_id')
        assert future['nodes']['effect']['output']['data']=={'mode':'future'}
        assert future['scheduled_at']==due.isoformat()
        assert ledger.read_text().splitlines()==['crash','future']
        assert s.svc.tick(due)==[] and execution_count(s,future['id'])==1 and execution_count(s,rid)==1
        retained=s.svc.get_run(rid)
        assert retained['nodes']==terminal['nodes'] and retained['status']=='failed'
        with s.store.transaction() as tx:assert len([r for r in tx.all('workflow_run') if r['workflow_id']==wf['id']])==2
    finally:
        release.touch()
        for pid,identity in owned:
            terminate_orphan(pid,identity,s.state/'workflow-runs'/rid)


@pytest.mark.parametrize('terminal_status',['failed','timed_out','cancelled'])
def test_recovery_reclaims_verified_worker_from_already_failed_run(tmp_path,terminal_status):
    """Focused persisted-state fault injection, supplementing the real kill trials."""
    import sys
    from taskconsole.store import Store, stamp
    from taskconsole.workflows import WorkflowService
    from taskconsole.workflows_n8n import recover_interrupted
    store=Store(tmp_path,'sqlite:///'+str(tmp_path/'state.sqlite'));svc=WorkflowService(store)
    wf=svc.save({'name':'Already failed adapter','nodes':[{'id':'effect','kind':'python','source':'def main(inputs): return {}','config':{},'inputs':{}}],'edges':[]});svc.publish(wf['id']);run=svc.admit(wf['id'])
    root=tmp_path/'workflow-runs'/run['id'];root.mkdir(parents=True)
    proc=subprocess.Popen([sys.executable,'-c','import time;time.sleep(90)'],cwd=root,start_new_session=True)
    identity=process_identity(proc.pid);assert identity
    try:
        with store.transaction() as tx:
            r=tx.get('workflow_run',run['id']);r.update(status=terminal_status,error='Original terminal root cause',finished_at='2026-09-17T01:02:03+00:00',adapter_finished_at='2026-09-17T01:02:04+00:00')
            r['nodes']['effect'].update(status='running',worker_pid=proc.pid,worker_identity=identity,process_started=True,attempts=[{'number':1,'status':'running'}]);tx.put('workflow_run',r)
        recover_interrupted(svc)
        until(lambda:proc.poll() is not None,timeout=3)
        recovered=svc.get_run(run['id'])
        assert recovered['status']==terminal_status
        assert recovered['error']=='Original terminal root cause'
        assert recovered['finished_at']=='2026-09-17T01:02:03+00:00'
        assert recovered['adapter_finished_at']=='2026-09-17T01:02:04+00:00'
        assert recovered.get('recovered_at') and 'uncertain' in recovered.get('recovery_note','')
        assert recovered['nodes']['effect']['status']=='not_run' and recovered['nodes']['effect']['reason']=='interrupted_unknown_effect'
        assert len(recovered['nodes']['effect']['attempts'])==1
        assert recovered['nodes']['effect']['attempts'][0]['status']=='failed'
    finally:
        terminate_orphan(proc.pid,identity,root);proc.wait(timeout=5);store.engine.dispose()
