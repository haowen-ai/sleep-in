"""Strict acceptance oracles; synthetic stores, subprocesses and loopback only."""
import copy
import json
import multiprocessing
import os
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_workflows import service, simple_graph, native_node
from test_workflow_api_contract import client, admin, saved
from taskconsole.schedule import workflow_next_runs
from test_workflow_execution_n8n_complete import live


def utc(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def execute(svc, run):
    for node in run['snapshot']['nodes']:
        svc.execute_node(run['id'], node['id'])
    result = svc.finish(run['id'])
    assert result['status'] == 'succeeded', result
    return result


def scheduled(tmp_path, spec, **options):
    svc = service(tmp_path)
    wf = svc.save(simple_graph())
    svc.publish(wf['id'])
    wf['triggers'] = [{'id': 'clock', 'kind': 'scheduled', 'enabled': True,
                       'schedule': spec, 'timezone': 'UTC', **options}]
    return svc, svc.save(wf, wf['id'])


def rows(svc, kind):
    with svc.store.transaction() as tx:
        return tx.all(kind)


def test_sch001_manual_preview_tick_and_one_manual_run(client):
    admin(client); wf = saved(client)
    prefix = '/api/workflows/' + wf['id']
    assert client.post(prefix + '/preview', json={'after': '2026-09-21T08:00Z'}).json()['next_runs'] == []
    assert client.post(prefix + '/publish').status_code == 200
    from taskconsole.workflows import WorkflowService
    svc = WorkflowService(client.app.state.store)
    assert svc.tick(utc('2026-09-21T08:00Z')) == []
    assert client.post(prefix + '/run', json={}).status_code == 202
    assert len(client.get('/api/workflow-runs').json()) == 1


def test_sch002_five_anchored_preview_instants_over_api(client):
    admin(client); wf = saved(client)
    result = client.post('/api/workflows/' + wf['id'] + '/preview', json={
        'schedule': {'kind': 'interval', 'every': 15, 'anchor': '2026-09-21T08:00Z'},
        'timezone': 'UTC', 'after': '2026-09-21T08:07Z'})
    assert result.status_code == 200
    assert [utc(x) for x in result.json()['next_runs'][:5]] == [utc('2026-09-21T' + x + 'Z') for x in ['08:15','08:30','08:45','09:00','09:15']]


@pytest.mark.parametrize('spec,before,due', [
    ({'kind':'interval','every':15,'anchor':'2026-09-21T08:00Z'}, '2026-09-21T08:29:59Z', '2026-09-21T08:30Z'),
    ({'kind':'interval','every':2,'unit':'hours','anchor':'2026-09-21T08:00Z'}, '2026-09-21T09:59:59Z', '2026-09-21T10:00Z'),
])
def test_sch003_sch004_sch022_exact_boundary_admission(tmp_path, spec, before, due):
    svc, wf = scheduled(tmp_path, spec)
    assert svc.tick(utc(before)) == []
    assert rows(svc, 'workflow_run') == []
    result = svc.tick(utc(due)); assert len(result) == 1
    assert result[0]['idempotency_key'] == 'schedule:' + utc(due).isoformat()
    execute(svc, result[0])
    assert svc.tick(utc(due)) == []
    assert svc.tick(utc(due) + timedelta(seconds=1)) == []
    assert len(rows(svc, 'workflow_run')) == 1


def test_sch006_sch007_multiple_daily_times_no_duplicate(tmp_path):
    spec = {'kind':'daily','times':['18:30','07:00','07:00']}
    svc, wf = scheduled(tmp_path, spec)
    assert workflow_next_runs(spec,'UTC',utc('2026-09-20T22:00Z'),count=3) == [utc(x) for x in ['2026-09-21T07:00Z','2026-09-21T18:30Z','2026-09-22T07:00Z']]
    for due in ['2026-09-21T07:00Z','2026-09-21T18:30Z']:
        svc.tick(utc(due) - timedelta(seconds=1))
        admitted = svc.tick(utc(due)); assert len(admitted) == 1
        execute(svc, admitted[0]); assert svc.tick(utc(due)) == []
    assert len(rows(svc,'workflow_occurrence')) == 2


def test_sch013_once_completion_keeps_manual_run_available(tmp_path):
    spec = {'kind':'once','date':'2026-09-21','time':'07:00'}
    svc, wf = scheduled(tmp_path,spec)
    assert svc.tick(utc('2026-09-21T06:59:59Z')) == []
    runs = svc.tick(utc('2026-09-21T07:00Z')); assert len(runs) == 1
    execute(svc,runs[0]); assert svc.tick(utc('2026-09-21T07:00:01Z')) == []
    assert workflow_next_runs(spec,'UTC',utc('2026-09-21T07:00:01Z')) == []
    manual = svc.admit(wf['id']); assert manual['trigger_id'] == 'manual'
    execute(svc,manual); assert len(rows(svc,'workflow_run')) == 2


def test_sch015_inclusive_boundaries_admit_only_two_days(tmp_path):
    spec = {'kind':'daily','time':'07:00','start':'2026-09-21T07:00Z','end':'2026-09-22T07:00Z'}
    svc,wf = scheduled(tmp_path,spec)
    for day in [21,22,23]:
        due = utc(f'2026-09-{day}T07:00Z');svc.tick(due-timedelta(seconds=1))
        admitted = svc.tick(due)
        assert len(admitted) == (1 if day < 23 else 0)
        for run in admitted: execute(svc,run)
    assert len(rows(svc,'workflow_run')) == 2


def test_sch019_dst_fold_admits_only_first_local_time(tmp_path):
    svc,wf = scheduled(tmp_path,{'kind':'daily','time':'01:30'})
    wf['triggers'][0]['timezone']='America/Chicago';svc.save(wf,wf['id'])
    for due, count in [('2026-11-01T06:30Z',1),('2026-11-01T07:30Z',0),('2026-11-02T07:30Z',1)]:
        svc.tick(utc(due)-timedelta(seconds=1));admitted=svc.tick(utc(due));assert len(admitted)==count
        for run in admitted:execute(svc,run)
    assert len(rows(svc,'workflow_run'))==2


def test_sch023_overlap_is_recorded_without_second_execution(tmp_path):
    svc,wf=scheduled(tmp_path,{'kind':'interval','every':1,'anchor':'2026-09-21T07:00Z'})
    svc.tick(utc('2026-09-21T06:59:59Z'));first=svc.tick(utc('2026-09-21T07:00Z'))[0]
    svc.tick(utc('2026-09-21T07:00:59Z'));assert svc.tick(utc('2026-09-21T07:01Z'))==[]
    assert len(rows(svc,'workflow_run'))==1
    assert [r['reason'] for r in rows(svc,'workflow_occurrence') if r['status']=='skipped']==['overlap_skipped']
    assert svc.get_run(first['id'])['status']=='queued'


def test_sch025_pause_and_resume_never_replay_missed_times(tmp_path):
    svc,wf=scheduled(tmp_path,{'kind':'daily','time':'07:00'})
    svc.tick(utc('2026-09-21T06:59:59Z'))
    wf['triggers'][0]['enabled']=False;svc.save(wf,wf['id'])
    assert svc.tick(utc('2026-09-21T07:00Z'))==[]
    wf['triggers'][0]['enabled']=True;svc.save(wf,wf['id'])
    assert svc.tick(utc('2026-09-21T08:00Z'))==[]
    svc.tick(utc('2026-09-22T06:59:59Z'));assert len(svc.tick(utc('2026-09-22T07:00Z')))==1
    assert len(rows(svc,'workflow_run'))==1


def test_sch027_sch029_missed_and_expired_grace_logged_without_runs(tmp_path):
    for name, options, recovered in [('skip',{},'2026-09-21T09:10Z'),('expired',{'missed_policy':'latest_once','grace_seconds':7200},'2026-09-21T11:00:01Z')]:
        svc,wf=scheduled(tmp_path/name,{'kind':'daily','times':['07:00','08:00','09:00']},**options)
        svc.tick(utc('2026-09-21T06:59:59Z'))
        assert svc.tick(utc(recovered))==[] and rows(svc,'workflow_run')==[]
        assert any(e['reason'].startswith('offline_gap') for e in rows(svc,'workflow_event'))
        svc.tick(utc('2026-09-22T06:59:59Z'));assert len(svc.tick(utc('2026-09-22T07:00Z')))==1


def _tick_worker(path, when, gate, result):
    svc=service(Path(path));gate.wait()
    try:result.put([r['id'] for r in svc.tick(utc(when))])
    except BaseException as exc:result.put({'error':repr(exc)})


@pytest.mark.parametrize('recovery',[False,True])
def test_sch021_sch028_two_processes_admit_once_and_side_effect_once(tmp_path,recovery):
    options={'missed_policy':'latest_once','grace_seconds':7200} if recovery else {}
    svc,wf=scheduled(tmp_path,{'kind':'daily','times':['07:00','08:00','09:00']},**options)
    counter=tmp_path/'effect.txt'
    wf['nodes'][0]['source']='from pathlib import Path\ndef main(inputs):\n p=Path('+repr(str(counter))+')\n p.write_text(p.read_text()+"1" if p.exists() else "1")\n return {"x":7}'
    svc.save(wf,wf['id']);svc.publish(wf['id'])
    svc.tick(utc('2026-09-21T06:59:59Z'))
    context=multiprocessing.get_context('spawn');gate=context.Barrier(2);result=context.Queue()
    when='2026-09-21T09:10Z' if recovery else '2026-09-21T07:00Z'
    processes=[context.Process(target=_tick_worker,args=(str(tmp_path),when,gate,result)) for _ in range(2)]
    for proc in processes:proc.start()
    for proc in processes:proc.join(15);assert proc.exitcode==0
    answers=[result.get(timeout=2) for _ in processes];assert all(isinstance(a,list) for a in answers),answers
    admitted=rows(svc,'workflow_run');assert len(admitted)==1
    assert admitted[0]['idempotency_key']=='schedule:'+utc('2026-09-21T09:00Z' if recovery else '2026-09-21T07:00Z').isoformat()
    with ThreadPoolExecutor(2) as pool:list(pool.map(lambda _:svc.execute_node(admitted[0]['id'],'a'),range(2)))
    execute(svc,admitted[0]);assert counter.read_text()=='1'


def test_sch035_backward_and_forward_clock_jump_never_replays(tmp_path):
    svc,wf=scheduled(tmp_path,{'kind':'interval','every':1,'anchor':'2026-09-21T07:00Z'})
    svc.tick(utc('2026-09-21T06:59:59Z'));run=svc.tick(utc('2026-09-21T07:00Z'))[0];execute(svc,run)
    assert svc.tick(utc('2026-09-21T06:50Z'))==[]
    assert svc.tick(utc('2026-09-21T07:00Z'))==[]
    assert svc.tick(utc('2026-09-21T07:10:31Z'))==[]
    assert len(rows(svc,'workflow_run'))==1
    assert any(e['reason']=='offline_gap_skipped' for e in rows(svc,'workflow_event'))


def test_sec010_artifacts_enforce_run_membership_with_valid_control(client):
    admin(client)
    from taskconsole.workflows import WorkflowService
    svc=WorkflowService(client.app.state.store);wf=svc.save(svc.templates()[0]);svc.publish(wf['id'])
    first=execute(svc,svc.admit(wf['id']));second=execute(svc,svc.admit(wf['id']))
    good=client.get(f'/api/workflow-runs/{first["id"]}/artifacts/{first["artifacts"][0]["id"]}')
    assert good.status_code==200 and good.text=='3 orders • 30.75\n'
    wrong=client.get(f'/api/workflow-runs/{first["id"]}/artifacts/{second["artifacts"][0]["id"]}')
    assert wrong.status_code==404


def test_sec006_sec007_sql_injection_and_readonly_cte_multistatement(tmp_path):
    from taskconsole.workflows_sql import create_connection,execute_sql
    svc=service(tmp_path);conn=create_connection(svc.store,{'name':'readonly','dialect':'sqlite','config':{'synthetic':True},'write_enabled':False})
    node={'id':'sql','kind':'sql','source':'SELECT * FROM orders WHERE order_id=:id','config':{'mode':'query','dialect':'sqlite','connection_id':conn['id']}}
    assert execute_sql(svc.store,node,{'id':"x'; DROP TABLE orders;--"},'wf')['output']['data']['rows']==[]
    for source in ['DELETE FROM orders','WITH keys AS (SELECT order_id FROM orders) DELETE FROM orders WHERE order_id IN (SELECT order_id FROM keys)','SELECT * FROM orders; DELETE FROM orders']:
        with pytest.raises(Exception):execute_sql(svc.store,{**node,'source':source},{},'wf')
        assert execute_sql(svc.store,{**node,'source':'SELECT * FROM orders'},{},'wf')['output']['data']['rowCount']==3


def test_sec013_shell_file_protocol_keeps_metacharacters_literal(tmp_path):
    from taskconsole.workflows_runtime import run_script
    marker=tmp_path/'must-not-exist';value='space "single\'; ; $(touch '+str(marker)+')'
    node={'id':'shell','kind':'shell','config':{},'source':'printf \'{"schemaVersion":1,"data":\' > "$SLEEP_IN_OUTPUT_FILE"\ncat "$SLEEP_IN_INPUT_FILE" >> "$SLEEP_IN_OUTPUT_FILE"\nprintf \',"artifacts":[]}\' >> "$SLEEP_IN_OUTPUT_FILE"'}
    result=run_script(node,{'value':value},tmp_path/'run',tmp_path)
    assert result['status']=='succeeded' and result['output']['data']=={'value':value}
    assert not marker.exists()


def test_sch026_pub002_queued_snapshot_and_latest_pinned_versions(tmp_path):
    svc,wf=scheduled(tmp_path,{'kind':'daily','time':'07:00'})
    wf['params']={'label':'v1'};wf['nodes'][1]['inputs']={'value':{'source':'node','node_id':'a','path':'x'},'label':{'source':'parameter','path':'label'}}
    svc.save(wf,wf['id']);v1=svc.publish(wf['id'])['version_id'];old=svc.admit(wf['id'])
    wf['nodes'][0]['source']='def main(inputs): return {"x":99,"new":100}'
    wf['nodes'][1]['inputs']['value']['path']='new';wf['params']['label']='v2'
    svc.save(wf,wf['id']);v2=svc.publish(wf['id'])['version_id']
    result=execute(svc,old)
    assert result['version_id']==v1 and result['params']=={'label':'v1'}
    assert result['nodes']['b']['output']['data']=={'received':{'value':7,'label':'v1'}}
    wf['triggers'][0]['version_id']=v1
    wf['triggers'].append({'id':'latest','kind':'scheduled','enabled':True,'schedule':{'kind':'daily','time':'08:00'},'timezone':'UTC'})
    svc.save(wf,wf['id'])
    for due,version,expected in [('2026-09-21T07:00Z',v1,7),('2026-09-21T08:00Z',v2,100)]:
        svc.tick(utc(due)-timedelta(seconds=1));run=svc.tick(utc(due))[0]
        assert run['version_id']==version
        assert execute(svc,run)['nodes']['b']['output']['data']['received']['value']==expected


def test_sch030_recovery_after_admission_commit_reuses_run(tmp_path,monkeypatch):
    svc,wf=scheduled(tmp_path,{'kind':'once','date':'2026-09-21','time':'07:00'})
    svc.tick(utc('2026-09-21T06:59:59Z'));admit=svc.admit
    class Crash(BaseException):pass
    def crash(*args,**kwargs):
        admit(*args,**kwargs)
        raise Crash('after run commit before occurrence acknowledgement')
    monkeypatch.setattr(svc,'admit',crash)
    with pytest.raises(Crash):svc.tick(utc('2026-09-21T07:00Z'))
    first=rows(svc,'workflow_run')[0]
    restarted=service(tmp_path)
    recovered=restarted.tick(utc('2026-09-21T07:00:31Z'))
    assert [r['id'] for r in recovered]==[first['id']]
    assert len(rows(restarted,'workflow_run'))==1
    execute(restarted,recovered[0]);assert restarted.tick(utc('2026-09-21T07:00:32Z'))==[]


def test_sch034_edit_schedule_preserves_active_snapshot_and_replaces_future_due(tmp_path):
    svc,wf=scheduled(tmp_path,{'kind':'interval','every':1,'anchor':'2026-09-21T07:00Z'},params={'label':'old'})
    svc.tick(utc('2026-09-21T06:59:59Z'));run=svc.tick(utc('2026-09-21T07:00Z'))[0]
    original=copy.deepcopy(run)
    wf['triggers'][0]['schedule']={'kind':'daily','time':'08:00'}
    wf['triggers'][0]['params']={'label':'new'};svc.save(wf,wf['id'])
    assert svc.get_run(run['id'])['snapshot']==original['snapshot']
    assert svc.get_run(run['id'])['params']=={'label':'old'};execute(svc,run)
    svc.tick(utc('2026-09-21T07:00:59Z'));assert svc.tick(utc('2026-09-21T07:01Z'))==[]
    svc.tick(utc('2026-09-21T07:59:59Z'));new=svc.tick(utc('2026-09-21T08:00Z'))
    assert len(new)==1 and new[0]['params']=={'label':'new'}
    assert len(rows(svc,'workflow_run'))==2


def test_sch040_independent_triggers_keep_occurrences_params_and_versions(tmp_path):
    svc,wf=scheduled(tmp_path,{'kind':'daily','time':'07:00'},params={'slot':'morning'})
    v1=rows(svc,'workflow_version')[0]['id']
    wf['nodes'][0]['source']='def main(inputs): return {"x":99}'
    svc.save(wf,wf['id']);v2=svc.publish(wf['id'])['version_id']
    wf['triggers'][0]['version_id']=v1
    wf['triggers'].append({'id':'second','kind':'scheduled','enabled':True,'schedule':{'kind':'daily','time':'08:00'},'timezone':'UTC','params':{'slot':'later'},'version_id':v2})
    svc.save(wf,wf['id'])
    for due,tid,params,version in [('2026-09-21T07:00Z','clock',{'slot':'morning'},v1),('2026-09-21T08:00Z','second',{'slot':'later'},v2)]:
        svc.tick(utc(due)-timedelta(seconds=1));run=svc.tick(utc(due))[0]
        assert (run['trigger_id'],run['params'],run['version_id'],run['idempotency_key'])==(tid,params,version,'schedule:'+utc(due).isoformat())
        occurrence=next(o for o in rows(svc,'workflow_occurrence') if o.get('run_id')==run['id'])
        assert occurrence['occurrence']==utc(due).isoformat() and occurrence['trigger_id']==tid
        execute(svc,run)
    assert len(rows(svc,'workflow_run'))==2


def test_pub003_draft_and_published_runs_execute_distinct_snapshots(client):
    admin(client);wf=saved(client);prefix='/api/workflows/'+wf['id']
    publication=client.post(prefix+'/publish').json()['version_id']
    wf['nodes'][0]['source']='def main(inputs): return {"value": 999}'
    assert client.put(prefix,json=wf).status_code==200
    from taskconsole.workflows import WorkflowService
    svc=WorkflowService(client.app.state.store)
    draft=client.post(prefix+'/run',json={'test':True}).json()
    assert draft['test'] and draft['version_id'] is None
    assert execute(svc,svc.get_run(draft['id']))['nodes']['source']['output']['data']=={'value':999}
    published=client.post(prefix+'/run',json={}).json()
    assert not published['test'] and published['version_id']==publication
    assert execute(svc,svc.get_run(published['id']))['nodes']['source']['output']['data']=={'value':7}
    assert client.get(prefix).json()['published_version_id']==publication


def test_pub012_three_attempts_with_backoff_consume_final_output_once(tmp_path):
    svc=service(tmp_path);graph=simple_graph({'value':{'source':'node','node_id':'a','path':'x'}})
    graph['nodes'][0]['config']['retry']={'max_attempts':3,'delay_seconds':.2,'safe_to_retry':True}
    graph['nodes'][0]['source']='import os\ndef main(inputs):\n if "attempt-3" not in os.environ["SLEEP_IN_INPUT_FILE"]: raise RuntimeError("transient")\n return {"x":7}'
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id'])
    for index in [1,2,3]:
        if index==1:svc.execute_node(run['id'],'a')
        else:
            assert not svc.node_status(run['id'],'a')['retry_ready']
            svc.submit_node(run['id'],'a',retry=True)
            assert len(svc.get_run(run['id'])['nodes']['a']['attempts'])==index-1
            time.sleep(.21);svc.submit_node(run['id'],'a',retry=True)
            deadline=time.monotonic()+5
            while svc.get_run(run['id'])['nodes']['a']['status'] in {'dispatching','running'} and time.monotonic()<deadline:time.sleep(.01)
    svc.execute_node(run['id'],'b');result=svc.finish(run['id'])
    assert result['status']=='succeeded'
    attempts=result['nodes']['a']['attempts'];assert [a['status'] for a in attempts]==['failed','failed','succeeded']
    assert len(result['nodes']['b']['attempts'])==1 and result['nodes']['b']['inputs']=={'value':7}
    assert all(a.get('started_at') and a.get('finished_at') for a in attempts)


def test_sec001_unauthenticated_and_revoked_sessions_cannot_read_run_data(client):
    admin(client)
    from taskconsole.workflows import WorkflowService
    svc=WorkflowService(client.app.state.store);wf=svc.save(svc.templates()[0]);svc.publish(wf['id']);run=execute(svc,svc.admit(wf['id']))
    paths=['/api/workflows','/api/workflows/'+wf['id'],'/api/connections','/api/workflow-runs/'+run['id'],f'/api/workflow-runs/{run["id"]}/nodes/report/output',f'/api/workflow-runs/{run["id"]}/artifacts/{run["artifacts"][0]["id"]}']
    old_cookies=dict(client.cookies);client.post('/api/logout')
    for cookies in [{},old_cookies]:
        client.cookies.clear();client.cookies.update(cookies)
        for path in paths:
            response=client.get(path);assert response.status_code==401,(path,response.text)
            assert str(client.app.state.store.path) not in response.text and '30.75' not in response.text


@pytest.mark.parametrize('failure',['missing','wrong','origin'])
def test_sec003_mutation_and_run_csrf_fail_without_state_change(client,failure):
    admin(client);wf=saved(client);prefix='/api/workflows/'+wf['id'];client.post(prefix+'/publish')
    before=client.get(prefix).json()
    if failure=='missing':del client.headers['X-CSRF-Token']
    elif failure=='wrong':client.headers['X-CSRF-Token']='wrong'
    else:client.headers['Origin']='https://unrelated.invalid'
    assert client.put(prefix,json={**wf,'name':'must-not-change'}).status_code==403
    assert client.post(prefix+'/run',json={}).status_code==403
    client.headers.pop('Origin',None)
    assert client.get(prefix).json()==before
    assert client.get('/api/workflow-runs').json()==[]


def test_sch038_new_cron_rejected_for_draft_save_preview_and_publish(client):
    admin(client);wf=saved(client);prefix='/api/workflows/'+wf['id']
    cron={**wf,'schedule':{'kind':'cron','cron':'* * * * *'}}
    rejected=client.put(prefix,json=cron)
    assert rejected.status_code in {400,422},rejected.text
    assert client.post(prefix+'/preview',json={'schedule':cron['schedule']}).status_code in {400,422}
    # A malformed stored record must not bypass publication validation.
    with client.app.state.store.transaction() as tx:
        item=tx.get('workflow',wf['id']);item['schedule']=cron['schedule'];tx.put('workflow',item)
    assert client.post(prefix+'/publish').status_code in {400,422}
    assert client.get('/api/workflow-runs').json()==[]


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='BLOCKED_ENV: installed n8n required')
def test_sch041_real_clock_n8n_all_nodes_and_result_download(live):
    from taskconsole.store import now
    from taskconsole.workflows_n8n import execute_graph
    import urllib.request
    svc=live;wf=svc.save(svc.templates()[0]);svc.publish(wf['id'])
    wf['enabled']=True;wf['schedule']={'kind':'interval','every':1,'unit':'minutes','anchor':(now()+timedelta(seconds=2)).isoformat()}
    svc.save(wf,wf['id']);before=len(rows(svc,'workflow_run'));assert svc.tick()==[]
    deadline=time.monotonic()+8;admitted=[]
    while not admitted and time.monotonic()<deadline:
        time.sleep(.1);admitted=svc.tick()
    assert len(admitted)==1 and len(rows(svc,'workflow_run'))==before+1
    assert svc.tick()==[]
    run=admitted[0];execute_graph(svc,run['id']);result=svc.get_run(run['id'])
    assert result['status']=='succeeded' and result.get('n8n_execution_id')
    assert result['nodes']['orders']['output']['data']['rowCount']==3
    assert len(result['nodes']['summary']['inputs']['orders'])==3
    assert result['nodes']['summary']['output']['data']=={'summary':{'count':3,'total':'30.75'}}
    assert result['nodes']['report']['inputs']=={'summary':{'count':3,'total':'30.75'}}
    assert all(n['status']=='succeeded' and len(n['attempts'])==1 for n in result['nodes'].values())
    item=result['artifacts'][0]
    with urllib.request.urlopen(os.environ['SLEEP_IN_BASE_URL']+f'/api/workflow-runs/{run["id"]}/artifacts/{item["id"]}') as response:
        assert response.read().decode()=='3 orders • 30.75\n'


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='BLOCKED_ENV: installed n8n required')
def test_sch005_daily_two_days_actual_n8n_with_controlled_clock(live):
    from taskconsole.workflows_n8n import execute_graph
    svc=live;wf=svc.save(svc.templates()[0]);svc.publish(wf['id'])
    wf['enabled']=True;wf['schedule']={'kind':'daily','time':'07:00'};svc.save(wf,wf['id'])
    assert workflow_next_runs(wf['schedule'],'UTC',utc('2026-09-20T22:00Z'),count=2)==[utc('2026-09-21T07:00Z'),utc('2026-09-22T07:00Z')]
    for day in [21,22]:
        due=utc(f'2026-09-{day}T07:00Z');svc.tick(due-timedelta(seconds=1));admitted=svc.tick(due);assert len(admitted)==1
        execute_graph(svc,admitted[0]['id']);result=svc.get_run(admitted[0]['id'])
        assert result['status']=='succeeded' and result.get('n8n_execution_id')
        assert result['nodes']['summary']['output']['data']=={'summary':{'count':3,'total':'30.75'}}
        assert svc.tick(due)==[]
    assert len(rows(svc,'workflow_run'))==2
    assert rows(svc,'workflow')[0]['enabled'] is True


def test_pub005_sample_stale_after_unpublished_upstream_edit(client):
    admin(client)
    from taskconsole.workflows import WorkflowService
    svc=WorkflowService(client.app.state.store);wf=svc.save(simple_graph({'value':{'source':'node','node_id':'a','path':'x'}}));svc.publish(wf['id']);run=execute(svc,svc.admit(wf['id']))
    path=f'/api/workflow-runs/{run["id"]}/nodes/b/sample'
    sample=client.get(path).json();assert sample['stale'] is False and sample['inputs']=={'value':7}
    wf['nodes'][0]['source']='def main(inputs): return {"x":999}'
    svc.save(wf,wf['id'])
    sample=client.get(path).json();assert sample['stale'] is True
    # Ordinary execution still consumes its admitted publication, never a sample value.
    svc.publish(wf['id']);fresh=execute(svc,svc.admit(wf['id']))
    assert fresh['nodes']['b']['inputs']=={'value':999}


@pytest.mark.parametrize('action',['cancel','timeout'])
def test_pub010_pub011_real_process_descendants_stop_before_terminal(tmp_path,action):
    svc=service(tmp_path);graph=simple_graph();counter=tmp_path/'child-count.txt'
    child='import time\nfrom pathlib import Path\np=Path('+repr(str(counter))+')\nwhile True:\n with p.open("a") as f:f.write("x")\n time.sleep(.05)'
    graph['nodes'][0]['source']='import subprocess,sys,time\ndef main(inputs):\n subprocess.Popen([sys.executable,"-c",'+repr(child)+'])\n time.sleep(10)\n return {}'
    graph['nodes'][0]['config']['timeout']=2
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id'])
    with ThreadPoolExecutor(1) as pool:
        future=pool.submit(svc.execute_node,run['id'],'a')
        deadline=time.monotonic()+4
        while (not counter.exists() or not counter.read_text()) and time.monotonic()<deadline:time.sleep(.02)
        assert counter.exists() and counter.read_text()
        if action=='cancel':svc.cancel(run['id'])
        state=future.result(timeout=6)
    assert state['status']==('cancelled' if action=='cancel' else 'timed_out')
    size=counter.stat().st_size;time.sleep(.25);assert counter.stat().st_size==size
    svc.execute_node(run['id'],'b');result=svc.finish(run['id'])
    assert result['nodes']['b']['attempts']==[]
    assert result['status']==('cancelled' if action=='cancel' else 'timed_out')


def test_sch031_committed_sql_lost_result_is_uncertain_never_replayed(tmp_path,monkeypatch):
    from taskconsole import workflows
    from taskconsole.workflows_sql import create_connection,execute_sql
    from taskconsole.workflows_n8n import recover_interrupted
    svc=service(tmp_path);conn=create_connection(svc.store,{'name':'counter','dialect':'sqlite','config':{'synthetic':True},'write_enabled':True})
    node={'id':'write','kind':'sql','source':"UPDATE orders SET amount=CAST(amount AS REAL)+1 WHERE order_id='A001'",'inputs':{},'config':{'connection_id':conn['id'],'dialect':'sqlite','mode':'write'}}
    wf=svc.save({'name':'Uncertain commit','nodes':[node],'edges':[]});svc.publish(wf['id']);run=svc.admit(wf['id'])
    class Crash(BaseException):pass
    original=workflows.execute_sql
    def lost_callback(*args,**kwargs):
        original(*args,**kwargs)
        raise Crash('commit persisted; result callback lost')
    monkeypatch.setattr(workflows,'execute_sql',lost_callback)
    with pytest.raises(Crash):svc.execute_node(run['id'],'write')
    query={**node,'source':"SELECT amount FROM orders WHERE order_id='A001'",'config':{**node['config'],'mode':'query'}}
    assert execute_sql(svc.store,query,{},wf['id'])['output']['data']['rows'][0]['amount']=='11.5'
    restarted=service(tmp_path);recover_interrupted(restarted);result=restarted.get_run(run['id'])
    assert 'uncertain' in result['error'] and result['nodes']['write']['reason']=='interrupted_unknown_effect'
    assert restarted.node_status(run['id'],'write')['retry_ready'] is False
    restarted.execute_node(run['id'],'write')
    assert execute_sql(svc.store,query,{},wf['id'])['output']['data']['rows'][0]['amount']=='11.5'


@pytest.mark.parametrize('spec,zone,after,expected',[
    ({'kind':'interval','every':2,'unit':'hours','anchor':'2026-09-21T08:00Z'},'UTC','2026-09-21T08:01Z',['2026-09-21T10:00Z','2026-09-21T12:00Z']),
    ({'kind':'daily','times':['18:30','07:00','07:00']},'UTC','2026-09-20T22:00Z',['2026-09-21T07:00Z','2026-09-21T18:30Z']),
    ({'kind':'daily','time':'07:00','start':'2026-09-21T07:00Z','end':'2026-09-22T07:00Z'},'UTC','2026-09-20T00:00Z',['2026-09-21T07:00Z','2026-09-22T07:00Z']),
    ({'kind':'daily','time':'07:00'},'America/Chicago','2026-09-20T00:00Z',['2026-09-20T12:00Z']),
    ({'kind':'daily','time':'07:00'},'Asia/Shanghai','2026-09-20T00:00Z',['2026-09-20T23:00Z']),
])
def test_schedule_fixed_api_oracles(client,spec,zone,after,expected):
    admin(client);wf=saved(client)
    response=client.post('/api/workflows/'+wf['id']+'/preview',json={'schedule':spec,'timezone':zone,'after':after})
    assert response.status_code==200,response.text
    assert [utc(v) for v in response.json()['next_runs'][:len(expected)]]==[utc(v) for v in expected]


@pytest.mark.parametrize('spec,zone,fragment',[
    ({'kind':'interval','every':0},'UTC','Interval'),({'kind':'interval','every':-1},'UTC','Interval'),
    ({'kind':'interval','every':1.5},'UTC','Interval'),({'kind':'interval','every':True},'UTC','Interval'),
    ({'kind':'daily','time':'25:00'},'UTC','HH:MM'),({'kind':'weekly','weekdays':[],'time':'07:00'},'UTC','weekday'),
    ({'kind':'monthly','day':32,'time':'07:00'},'UTC','day'),({'kind':'daily','time':'07:00'},'Not/AZone','timezone'),
    ({'kind':'daily','time':'07:00','start':'2026-09-22T07:00Z','end':'2026-09-21T07:00Z'},'UTC','End'),
])
def test_schedule_invalid_api_values_are_specific_without_runs(client,spec,zone,fragment):
    admin(client);wf=saved(client);prefix='/api/workflows/'+wf['id']
    response=client.post(prefix+'/preview',json={'schedule':spec,'timezone':zone})
    assert response.status_code in {400,422}
    assert fragment.lower() in response.json()['detail']['message'].lower()
    assert client.post(prefix+'/publish').status_code==200
    response=client.put(prefix,json={**wf,'enabled':True,'schedule':spec,'timezone':zone})
    assert response.status_code in {400,422}
    assert fragment.lower() in response.json()['detail']['message'].lower()
    assert client.get('/api/workflow-runs').json()==[]


def test_sch033_unpublished_schedule_enable_rejected_draft_preserved(client):
    admin(client);wf=saved(client);prefix='/api/workflows/'+wf['id']
    response=client.put(prefix,json={**wf,'enabled':True,'schedule':{'kind':'daily','time':'07:00'}})
    assert response.status_code in {400,422} and 'publish' in response.text.lower()
    assert client.get(prefix).json()==wf
    assert client.get('/api/workflow-runs').json()==[]


def test_pub001_invalid_draft_retains_errors_but_publish_identifies_node(client):
    admin(client);wf=saved(client);wf['nodes'][0]={'id':'source','name':'Incomplete','kind':'sql','source':'SELECT :required','config':{'dialect':'sqlite'},'inputs':{'required':{'source':'node','node_id':'absent','path':'value'}}}
    prefix='/api/workflows/'+wf['id'];response=client.put(prefix,json=wf)
    assert response.status_code==200 and response.json()['validation_errors']
    assert all(e.get('node_id')=='source' for e in response.json()['validation_errors'])
    response=client.post(prefix+'/publish');assert response.status_code in {400,422}
    assert response.json()['detail']['node_id']=='source'
    assert client.get(prefix).json()['published_version_id'] is None


def test_pub009_runtime_dependency_update_only_new_publication_uses_new_value(tmp_path):
    from taskconsole.workflows_packs import RuntimePacks
    svc=service(tmp_path);packs=RuntimePacks(svc.store)
    profile=packs.create({'name':'Shared','language':'python','config':{'shared_files':{'shared.py':'VALUE=7'}}})
    v1=packs.build(profile['id']);graph=simple_graph();graph['nodes']=graph['nodes'][:1];graph['edges']=[]
    graph['nodes'][0].update(source='import shared\ndef main(inputs): return {"x":shared.VALUE}',config={'runtime_version_id':v1['id']})
    wf=svc.save(graph);old=svc.publish(wf['id'])['version_id']
    v2=packs.new_version(profile['id'],{'shared_files':{'shared.py':'VALUE=9'}});v2=packs.build(profile['id'],v2['id'])
    assert execute(svc,svc.admit(wf['id']))['nodes']['a']['output']['data']=={'x':7}
    wf['nodes'][0]['config']['runtime_version_id']=v2['id'];svc.save(wf,wf['id']);svc.publish(wf['id'])
    assert execute(svc,svc.admit(wf['id']))['nodes']['a']['output']['data']=={'x':9}
    assert execute(svc,svc.admit(wf['id'],version_id=old))['nodes']['a']['output']['data']=={'x':7}


@pytest.fixture
def notification_stub():
    import threading
    from http.server import BaseHTTPRequestHandler,HTTPServer
    received=[];codes=[500]
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            self.send_response(codes[0]);self.end_headers()
        def log_message(self,*args):pass
    server=HTTPServer(('127.0.0.1',0),Handler);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:yield f'http://127.0.0.1:{server.server_port}',received,codes
    finally:server.shutdown();server.server_close();thread.join(2)


def test_pub017_notification_retry_keeps_business_success_and_is_authorized_idempotent(client,notification_stub):
    admin(client)
    from taskconsole.workflows import WorkflowService
    from taskconsole.workflows_operations import WorkflowOperations
    svc=WorkflowService(client.app.state.store);op=WorkflowOperations(svc.store);url,received,codes=notification_stub
    channel=op.save_channel({'name':'Local acceptance','kind':'webhook','config':{'url':url}})
    graph=simple_graph();graph['nodes']=graph['nodes'][:1];graph['edges']=[]
    graph['notifications']={'channel_ids':[channel['id']],'events':['succeeded']}
    wf=svc.save(graph);svc.publish(wf['id']);run=execute(svc,svc.admit(wf['id']));op.tick()
    delivery=op.deliveries()[0];assert delivery['status']=='failed' and len(received)==1
    endpoint='/api/workflow-notifications/'+delivery['id']+'/retry'
    token=client.headers.pop('X-CSRF-Token')
    assert client.post(endpoint,json={'expected_attempt':1}).status_code==403
    client.headers['X-CSRF-Token']=token
    response=client.post(endpoint,json={'expected_attempt':1});assert response.status_code==200,response.text
    assert response.json()['status']=='pending'
    assert client.post(endpoint,json={'expected_attempt':1}).json()['status']=='pending'
    codes[0]=204;op.tick()
    assert len(received)==2 and op.deliveries()[0]['status']=='sent' and op.deliveries()[0]['attempts']==2
    assert client.post(endpoint,json={'expected_attempt':1}).json()['status']=='sent';op.tick();assert len(received)==2
    final=svc.get_run(run['id']);assert final['status']=='succeeded' and len(final['nodes']['a']['attempts'])==1
    assert len(rows(svc,'workflow_run'))==1
    client.post('/api/admin/users',json={'username':'operator','password':'operator acceptance password','role':'operator'})
    client.post('/api/logout');login=client.post('/api/login',json={'username':'operator','password':'operator acceptance password'})
    client.headers['X-CSRF-Token']=login.json()['csrf']
    assert client.post(endpoint,json={'expected_attempt':2}).status_code==403


def test_pub007_missing_selected_sql_driver_blocks_publication(client,monkeypatch):
    admin(client)
    from taskconsole import workflows_sql
    monkeypatch.setattr(workflows_sql,'driver_status',lambda dialect: dialect=='sqlite')
    connection=client.post('/api/connections',json={'name':'Unavailable synthetic driver','dialect':'oracle','config':{'dsn':'uncontacted.invalid'}}).json()
    assert connection['driver_available'] is False
    wf=client.post('/api/workflows',json={'name':'Driver check','nodes':[{'id':'sql','kind':'sql','source':'SELECT 1 FROM dual','inputs':{},'config':{'dialect':'oracle','connection_id':connection['id']}}],'edges':[]}).json()
    result=client.post('/api/workflows/'+wf['id']+'/publish')
    assert result.status_code in {400,422},result.text
    assert 'driver' in result.text.lower() and 'oracle' in result.text.lower()
    assert result.json()['detail']['node_id']=='sql'
    assert client.get('/api/workflows/'+wf['id']).json()['published_version_id'] is None


def test_pub016_only_final_published_failure_notifies_once(tmp_path,notification_stub):
    from taskconsole.workflows_operations import WorkflowOperations
    url,received,codes=notification_stub;codes[0]=204;svc=service(tmp_path);op=WorkflowOperations(svc.store)
    channel=op.save_channel({'name':'Local failure','kind':'webhook','config':{'url':url}})
    graph=simple_graph();graph['nodes']=graph['nodes'][:1];graph['edges']=[]
    graph['nodes'][0]['source']='def main(inputs): raise RuntimeError("synthetic failure")'
    graph['nodes'][0]['config']['retry']={'max_attempts':3,'safe_to_retry':True,'delay_seconds':0}
    graph['notifications']={'channel_ids':[channel['id']],'events':['failed']}
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id']);svc.execute_node(run['id'],'a')
    for attempt in [2,3]:
        op.tick();assert received==[]
        svc.submit_node(run['id'],'a',retry=True)
        deadline=time.monotonic()+5
        while svc.get_run(run['id'])['nodes']['a']['status'] in {'running','dispatching'} and time.monotonic()<deadline:time.sleep(.01)
    assert svc.finish(run['id'])['status']=='failed';op.tick();op.tick();assert len(received)==1
    draft=svc.admit(wf['id'],test=True);svc.execute_node(draft['id'],'a');svc.finish(draft['id']);op.tick()
    assert len(received)==1 and received[0]['run_id']==run['id']


def test_sch039_naive_boundary_uses_selected_zone_first_fold_and_rejects_gap(client):
    admin(client);wf=saved(client);prefix='/api/workflows/'+wf['id']
    value={**wf,'timezone':'America/Chicago','schedule':{'kind':'daily','time':'01:30','start':'2026-11-01T01:30','end':'2026-11-02T01:30'}}
    response=client.put(prefix,json=value);assert response.status_code==200,response.text
    stored=response.json()['schedule']
    assert utc(stored['start'])==utc('2026-11-01T06:30Z')
    assert utc(stored['end'])==utc('2026-11-02T07:30Z')
    assert utc(stored['start']).tzinfo is not None
    preview=client.post(prefix+'/preview',json={'after':'2026-11-01T05:00Z'})
    assert [utc(v) for v in preview.json()['next_runs']]==[utc('2026-11-01T06:30Z'),utc('2026-11-02T07:30Z')]
    value['schedule']={'kind':'daily','time':'02:30','start':'2026-03-08T02:30'}
    response=client.put(prefix,json=value)
    assert response.status_code in {400,422} and 'nonexistent' in response.text.lower()
    assert client.get(prefix).json()['schedule']==stored


def test_sch024_queue_limit_two_keeps_fifo_and_admission_snapshots(tmp_path,monkeypatch):
    from taskconsole import workflows_n8n
    svc,wf=scheduled(tmp_path,{'kind':'interval','every':1,'anchor':'2026-09-21T07:00Z'},overlap='queue',queue_limit=2)
    admitted=[]
    for minute in range(4):
        wf['triggers'][0]['params']={'position':minute};svc.save(wf,wf['id'])
        due=utc('2026-09-21T07:00Z')+timedelta(minutes=minute)
        svc.tick(due-timedelta(seconds=1));result=svc.tick(due)
        assert len(result)==(1 if minute<3 else 0)
        admitted.extend(result)
    assert len(rows(svc,'workflow_run'))==3
    assert [o['reason'] for o in rows(svc,'workflow_occurrence') if o['status']=='skipped']==['overlap_skipped']
    seen=[]
    def runner(service,rid):
        run=service.get_run(rid);seen.append(run['params']['position']);execute(service,run)
        from taskconsole.store import stamp
        with service.store.transaction() as tx:
            completed=tx.get('workflow_run',rid);completed['adapter_finished_at']=stamp();tx.put('workflow_run',completed)
    monkeypatch.setattr(workflows_n8n,'execute_graph',runner)
    for run in admitted:
        assert workflows_n8n.dispatch_pending(svc)==[run['id']]
        svc._threads[run['id']].join(5);assert not svc._threads[run['id']].is_alive()
    assert seen==[0,1,2] and workflows_n8n.dispatch_pending(svc)==[]


def test_pub014_concurrent_graph_claim_and_duplicate_execution_only_one_effect(tmp_path):
    import threading
    svc=service(tmp_path);counter=tmp_path/'effect';graph=simple_graph();graph['nodes']=graph['nodes'][:1];graph['edges']=[]
    graph['nodes'][0]['source']='from pathlib import Path\ndef main(inputs):\n p=Path('+repr(str(counter))+')\n p.write_text(p.read_text()+"x" if p.exists() else "x")\n return {}'
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id']);gate=threading.Barrier(2)
    def start(owner):
        gate.wait()
        try:svc.claim_graph(run['id'],owner)
        except ValueError:return False
        svc.execute_node(run['id'],'a');return True
    with ThreadPoolExecutor(2) as pool:assert sorted(pool.map(start,['one','two']))==[False,True]
    svc.execute_node(run['id'],'a');final=svc.finish(run['id'])
    assert final['status']=='succeeded' and counter.read_text()=='x'
    assert len(final['nodes']['a']['attempts'])==1 and final['graph_execution_id'] in {'one','two'}


def test_pub015_late_duplicate_submission_keeps_terminal_attempt_evidence(client):
    admin(client)
    from taskconsole.workflows import WorkflowService
    svc=WorkflowService(client.app.state.store);wf=svc.save(simple_graph());svc.publish(wf['id']);run=execute(svc,svc.admit(wf['id']))
    before=copy.deepcopy(run['nodes']['a'])
    response=client.post(f'/internal/workflows/{run["id"]}/nodes/a',headers={'x-workflow-token':run['callback_token']})
    assert response.status_code==200 and response.json()['status']=='succeeded'
    after=svc.get_run(run['id'])['nodes']['a'];assert after==before and len(after['attempts'])==1
    assert utc(after['attempts'][0]['started_at'])<=utc(after['attempts'][0]['finished_at'])


def test_notification_retry_rejects_uncertain_and_duplicate_old_failure(tmp_path):
    from taskconsole.workflows_operations import WorkflowOperations
    svc=service(tmp_path);op=WorkflowOperations(svc.store)
    with svc.store.transaction() as tx:
        tx.put('workflow_notification',{'id':'uncertain','status':'uncertain','attempts':1})
        tx.put('workflow_notification',{'id':'newer','status':'failed','attempts':2})
    with pytest.raises(ValueError,match='uncertain'):op.retry_delivery('uncertain',1)
    assert op.retry_delivery('newer',1)['status']=='failed'
    with pytest.raises(ValueError):op.retry_delivery('newer',True)
    assert op.retry_delivery('newer',2)['status']=='pending'


def test_sec005_connection_failure_never_exposes_resolved_secret(client,monkeypatch):
    import secrets
    from taskconsole import workflows_sql
    from taskconsole.workflows import WorkflowService
    from taskconsole.workflows_transfer import export_template
    admin(client);secret='synthetic-'+secrets.token_hex(12)
    connection=client.post('/api/connections',json={'name':'Synthetic','dialect':'sqlite','config':{'synthetic':True,'password':secret}})
    assert secret not in connection.text
    cid=connection.json()['id'];svc=WorkflowService(client.app.state.store)
    wf=svc.save({'name':'Failure masking','nodes':[{'id':'sql','kind':'sql','source':'SELECT * FROM orders','config':{'connection_id':cid,'dialect':'sqlite'},'inputs':{}}],'edges':[]})
    svc.publish(wf['id']);run=svc.admit(wf['id'])
    observed=[]
    def failed_connect(store,record,*args,**kwargs):
        config=json.loads(store.fernet.decrypt(record['encrypted_config'].encode()));observed.append(config['password'])
        raise ValueError('Synthetic driver failure with password '+config['password'])
    monkeypatch.setattr(workflows_sql,'connect',failed_connect)
    svc.execute_node(run['id'],'sql');svc.finish(run['id'])
    assert observed==[secret]
    for response in [client.get('/api/connections'),client.get('/api/workflows/'+wf['id']),client.get('/api/workflow-runs'),client.get('/api/workflow-runs/'+run['id'])]:
        assert response.status_code==200 and secret not in response.text
    assert secret not in json.dumps(export_template(svc.store,wf['id']))
    result=svc.get_run(run['id']);assert secret not in json.dumps(result) and '[redacted]' in result['nodes']['sql']['error']
