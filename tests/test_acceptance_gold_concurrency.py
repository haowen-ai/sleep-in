"""Exact golden traces and real scheduler-process/queue contention."""
from datetime import datetime, timedelta, timezone
import copy
import hashlib
import json
import os
from pathlib import Path
import signal
import sqlite3
import subprocess
import sys
import time

import pytest
from test_acceptance_data import live, configured_node, ORDERS
from test_acceptance_local_recovery import until
from taskconsole.workflows_n8n import command, execute_graph
from taskconsole.workflows_runtime import resolve_path

pytestmark=pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='Pinned actual n8n required')


def golden(svc):
    graph=svc.templates()[0]
    for node in graph['nodes']:
        if node['kind']=='python':node['source']=node['source'].replace('def main(inputs):','def main(inputs):\n    from pathlib import Path\n    Path("business-calls").open("a").write("1\\n")')
        elif node['kind']=='javascript':node['source']='require("fs").appendFileSync("business-calls","1\\n");\n'+node['source']
    return graph


def publish(svc,graph):
    client=svc.acceptance_client;response=client.post('/api/workflows',json=graph);assert response.status_code==200,response.text
    wf=response.json();response=client.post('/api/workflows/'+wf['id']+'/publish');assert response.status_code==200,response.text
    return wf,response.json()


def invocation(svc,run,nid,expected=1):
    path=svc.store.path/'workflow-runs'/run['id']/nid/'attempt-1'/'project'/'business-calls'
    assert len(run['nodes'][nid]['attempts'])==expected
    if expected:assert path.read_text()=='1\n'
    else:assert not path.exists() and not run['nodes'][nid].get('process_started')


def wait_run(svc,rid,timeout=150):
    return until(lambda:(r if (r:=svc.get_run(rid)).get('adapter_finished_at') else None),timeout=timeout)


def decode_flatted(raw):
    """Read the pinned n8n persisted execution format, including shared references."""
    flat=json.loads(raw)
    if isinstance(flat,dict):return flat
    memo={}
    def ref(value):
        if isinstance(value,str) and value.isdigit():return entry(int(value))
        return value
    def entry(index):
        if index in memo:return memo[index]
        value=flat[index]
        if isinstance(value,dict):
            target={};memo[index]=target;target.update({k:ref(v) for k,v in value.items()});return target
        if isinstance(value,list):
            target=[];memo[index]=target;target.extend(ref(v) for v in value);return target
        memo[index]=value;return value
    return entry(0)


def trace(svc,run):
    path=svc.store.path/'workflow-runs'/run['id']/'n8n'/'.n8n'/'database.sqlite'
    with sqlite3.connect('file:'+str(path)+'?mode=ro',uri=True) as db:
        rows=db.execute('SELECT id FROM execution_entity WHERE workflowId=?',(run['id'],)).fetchall()
        assert len(rows)==1 and str(rows[0][0])==run['n8n_execution_id']==run['graph_execution_id']
        data=db.execute('SELECT data FROM execution_data WHERE executionId=?',(rows[0][0],)).fetchone()
    assert data
    return decode_flatted(data[0])['resultData']['runData']


def test_gold07_missing_summary_path_is_actionable_and_report_never_invoked(live):
    graph=golden(live);report=graph['nodes'][2]
    graph['nodes'][1]['outputs']={}  # Deliberately dynamic: absence is unknowable at publication.
    report['inputs']['summary']['path']=['summary','missing']
    with pytest.raises(KeyError):resolve_path({'summary':{'count':3,'total':'30.75'}},['summary','missing'])
    wf,_=publish(live,graph);response=live.acceptance_client.post('/api/workflows/'+wf['id']+'/run',json={});assert response.status_code==202
    execute_graph(live,response.json()['id']);run=live.get_run(response.json()['id'])
    assert run['status']=='failed' and run.get('n8n_execution_id') and run.get('graph_execution_id')
    assert live.acceptance_sql_calls['orders']==1 and run['nodes']['orders']['output']['data']['rows']==ORDERS
    assert run['nodes']['summary']['output']['data']=={'summary':{'count':3,'total':'30.75'}}
    invocation(live,run,'summary');invocation(live,run,'report',0)
    state=run['nodes']['report'];assert state['status']=='failed'
    assert state['error_detail']['node_id']=='report' and state['error_detail']['field']=='summary'
    assert 'report.summary from summary path ["summary", "missing"]' in state['error']
    assert run['artifacts']==[]


def test_gold10_authenticated_schedule_duplicate_tick_and_pinned_n8n_callback_trace(live):
    actual_version=subprocess.run(command()+['--version'],capture_output=True,text=True,timeout=30)
    assert actual_version.returncode==0 and actual_version.stdout.strip()=='2.39.7'
    graph=golden(live);wf,pub=publish(live,graph);due=datetime.now(timezone.utc)+timedelta(seconds=3)
    trigger={'id':'clock','kind':'scheduled','enabled':True,'schedule':{'kind':'interval','every':1440,'unit':'minutes','anchor':due.isoformat()},'timezone':'UTC','params':{'operation_key':'gold-fixed-occurrence'}}
    updated=live.acceptance_client.put('/api/workflows/'+wf['id'],json={**wf,'triggers':[trigger]});assert updated.status_code==200,updated.text
    headers={'x-dispatch-token':(live.store.path/'dispatch-token').read_text().strip()}
    def tick():
        response=live.acceptance_client.post('/internal/workflow-tick',headers=headers,json={});assert response.status_code==200,response.text;return response.json()['admitted']
    assert tick()==0
    admitted=until(lambda:tick(),timeout=10);assert admitted==1
    runs=live.acceptance_client.get('/api/workflow-runs').json();assert len(runs)==1;rid=runs[0]['id']
    assert tick()==tick()==0
    try:
        run=wait_run(live,rid);assert run['status']=='succeeded',run.get('adapter_log')
        assert run['version_id']==pub['version_id'] and run['params']=={'operation_key':'gold-fixed-occurrence'}
        assert run['idempotency_key']=='schedule:'+due.isoformat() and run['scheduled_at']==due.isoformat()
        assert live.acceptance_sql_calls['orders']==1
        for nid in ['summary','report']:invocation(live,run,nid)
        assert all(n['status']=='succeeded' and len(n['attempts'])==1 for n in run['nodes'].values())
        assert run['nodes']['orders']['output']['data']['rows']==ORDERS and run['nodes']['orders']['output']['data']['rowCount']==3
        assert run['nodes']['summary']['inputs']=={'orders':ORDERS}
        assert run['nodes']['summary']['output']['data']=={'summary':{'count':3,'total':'30.75'}}
        assert run['nodes']['report']['inputs']=={'summary':{'count':3,'total':'30.75'}}
        assert run['nodes']['report']['output']['data']=={'message':'3 orders • 30.75'}
        artifact=run['artifacts'][0];raw='3 orders • 30.75\n'.encode()
        download=live.acceptance_client.get(f'/api/workflow-runs/{rid}/artifacts/{artifact["id"]}')
        assert download.status_code==200 and download.content==raw and artifact['sha256']==hashlib.sha256(raw).hexdigest()
        emitted=json.loads((live.store.path/'workflow-runs'/rid/'n8n-graph.json').read_text())
        for source,target in [('orders','summary'),('summary','report')]:
            assert any(edge['node']=='submit_'+target for edge in emitted['connections']['node_'+source]['main'][0])
        assert {n['name'] for n in emitted['nodes']}>={'submit_orders','submit_summary','submit_report','finish'}
        records=trace(live,run)
        for nid in ['orders','summary','report']:
            assert 'submit_'+nid in records and 'node_'+nid in records
            payloads=[attempt['data']['main'][0][0]['json'] for attempt in records['status_'+nid]]
            assert any(p['node_id']==nid and p['terminal'] and p['status']=='succeeded' for p in payloads)
        assert records['finish'][-1]['data']['main'][0][0]['json']['status']=='succeeded'
        assert tick()==0 and len(live.acceptance_client.get('/api/workflow-runs').json())==1
    finally:
        if not live.get_run(rid).get('adapter_finished_at'):live.cancel(rid);wait_run(live,rid,30)


def test_two_real_scheduler_processes_claim_one_queued_graph(live):
    ledger=live.store.path/'effects';node={'id':'effect','kind':'python','config':{},'inputs':{},'source':f'import os\nfrom pathlib import Path\ndef main(inputs):\n with Path({str(ledger)!r}).open("a") as f: f.write("effect-001\\n");f.flush();os.fsync(f.fileno())\n return {{"effect":1}}'}
    wf,_=publish(live,{'name':'Two OS schedulers','nodes':[node],'edges':[]})
    response=live.acceptance_client.post('/api/workflows/'+wf['id']+'/run',json={'idempotency_key':'same-queued-record'});assert response.status_code==202;rid=response.json()['id']
    script='''import os,sys,time,json
from pathlib import Path
from taskconsole.store import Store
from taskconsole.workflows import WorkflowService
root=Path(sys.argv[1]);tag=sys.argv[2];store=Store(root,'sqlite:///'+str(root/'state.sqlite'));svc=WorkflowService(store)
(root/(tag+'.ready')).write_text(str(os.getpid()))
while not (root/'release-dispatch').exists():time.sleep(.01)
launched=svc.dispatch_pending()
(root/(tag+'.result')).write_text(json.dumps({'pid':os.getpid(),'launched':launched}))
for rid in launched:svc._threads[rid].join(150)
assert all(not t.is_alive() for t in svc._threads.values())
store.engine.dispose()
'''
    children=[]
    try:
        for tag in ['one','two']:
            children.append(subprocess.Popen([sys.executable,'-c',script,str(live.store.path),tag],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True))
        until(lambda:all((live.store.path/(tag+'.ready')).exists() and (live.store.path/(tag+'.ready')).stat().st_size for tag in ['one','two']),timeout=15)
        assert len({(live.store.path/(tag+'.ready')).read_text() for tag in ['one','two']})==2
        (live.store.path/'release-dispatch').touch()
        until(lambda:all((live.store.path/(tag+'.result')).exists() and (live.store.path/(tag+'.result')).stat().st_size for tag in ['one','two']),timeout=15)
        results=[json.loads((live.store.path/(tag+'.result')).read_text()) for tag in ['one','two']]
        assert sorted(len(r['launched']) for r in results)==[0,1] and [r for v in results for r in v['launched']]==[rid]
        run=wait_run(live,rid);assert run['status']=='succeeded' and run.get('adapter_lease') and run.get('adapter_execution_owner')
        assert ledger.read_text().splitlines()==['effect-001'] and len(run['nodes']['effect']['attempts'])==1
        assert run['nodes']['effect']['output']['data']=={'effect':1}
        assert 'node_effect' in trace(live,run)
        for process in children:
            out,err=process.communicate(timeout=15);assert process.returncode==0,(out,err)
        assert live.dispatch_pending()==[] and ledger.read_text().splitlines()==['effect-001']
    finally:
        if not live.get_run(rid).get('adapter_finished_at'):
            live.cancel(rid)
            deadline=time.monotonic()+30
            while any(p.poll() is None for p in children) and time.monotonic()<deadline:time.sleep(.05)
        for process in children:
            if process.poll() is None:
                os.killpg(process.pid,signal.SIGTERM)
                try:process.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(process.pid,signal.SIGKILL);process.wait()


def test_authenticated_bounded_queue_drains_sequentially_with_frozen_parameters(live):
    ledger=live.store.path/'queue-effects';release=live.store.path/'release-first'
    source=f'''import os,time
from pathlib import Path
def main(inputs):
 seq=inputs['seq']
 with Path({str(ledger)!r}).open('a') as f:f.write(str(seq)+'\\n');f.flush();os.fsync(f.fileno())
 if seq==1:
  while not Path({str(release)!r}).exists():time.sleep(.02)
 return {{'seq':seq}}
'''
    node={'id':'effect','kind':'python','source':source,'config':{'timeout':120},'inputs':{'seq':{'source':'parameter','path':['seq']}}}
    graph={'name':'Bounded FIFO','timeout':180,'nodes':[node],'edges':[],'params':{'seq':0},'triggers':[{'id':'queue','kind':'api','enabled':False,'overlap':'queue','queue_limit':2},{'id':'default','kind':'api','enabled':False}]}
    wf,_=publish(live,graph)
    for trigger in wf['triggers']:trigger['enabled']=True
    response=live.acceptance_client.put('/api/workflows/'+wf['id'],json=wf);assert response.status_code==200,response.text
    wf=response.json();prefix='/api/workflows/'+wf['id']+'/triggers/'
    def enqueue(seq,trigger='queue'):
        return live.acceptance_client.post(prefix+trigger+'/run',json={'params':{'seq':seq},'idempotency_key':f'seq-{seq}'})
    first=enqueue(1);assert first.status_code==202,first.text;rid=first.json()['id'];ids=[rid]
    try:
        assert live.dispatch_pending()==[rid]
        until(lambda:ledger.exists(),timeout=70)
        assert live.get_run(rid)['nodes']['effect']['status']=='running'
        default=enqueue(99,'default');assert default.status_code==409 and default.json()['detail']['code']=='workflow_active'
        for seq in [2,3]:
            response=enqueue(seq);assert response.status_code==202,response.text;ids.append(response.json()['id'])
        rejected=enqueue(4);assert rejected.status_code==409 and rejected.json()['detail']['code']=='workflow_active'
        assert live.dispatch_pending()==[] and ledger.read_text().splitlines()==['1']
        assert all(live.get_run(r)['nodes']['effect']['attempts']==[] for r in ids[1:])
        changed=copy.deepcopy(wf);changed['params']={'seq':999}
        due=(datetime.now(timezone.utc)+timedelta(days=1)).replace(second=0,microsecond=0)
        changed['triggers'].append({'id':'scheduled-default','kind':'scheduled','enabled':True,'schedule':{'kind':'once','date':due.date().isoformat(),'time':due.strftime('%H:%M')},'timezone':'UTC','params':{'seq':999}})
        assert live.acceptance_client.put('/api/workflows/'+wf['id'],json=changed).status_code==200
        live.tick(due-timedelta(seconds=1));assert live.tick(due)==[]
        with live.store.transaction() as tx:
            occurrences=tx.all('workflow_occurrence');assert len(occurrences)==1 and occurrences[0]['status']=='skipped' and occurrences[0]['reason']=='overlap_skipped'
        release.touch();runs=[wait_run(live,rid)]
        for next_id in ids[1:]:
            assert live.dispatch_pending()==[next_id]
            runs.append(wait_run(live,next_id))
        assert ledger.read_text().splitlines()==['1','2','3'] and live.dispatch_pending()==[]
        for seq,run in enumerate(runs,1):
            assert run['status']=='succeeded' and run['params']=={'seq':seq}
            assert run['nodes']['effect']['inputs']==run['nodes']['effect']['output']['data']=={'seq':seq}
            assert len(run['nodes']['effect']['attempts'])==1 and 'node_effect' in trace(live,run)
            if seq>1:assert runs[seq-2]['finished_at']<=run['started_at']
        assert len(live.acceptance_client.get('/api/workflow-runs').json())==3
    finally:
        release.touch()
        for run_id in ids:
            if not live.get_run(run_id).get('adapter_finished_at'):live.cancel(run_id)
        for thread in live._threads.values():thread.join(15)
