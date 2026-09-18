"""Strict remaining acceptance preconditions: real active processes and provenance."""
import copy
import time
from datetime import timedelta

import pytest
from test_workflows import service, simple_graph, native_node
from test_workflow_api_contract import client, admin
from test_acceptance_schedule_security import utc, execute, rows, scheduled


def test_sch040_public_scheduled_instant_is_separate_from_actual_start(client):
    admin(client)
    from taskconsole.workflows import WorkflowService
    svc=WorkflowService(client.app.state.store);wf=svc.save(simple_graph());svc.publish(wf['id'])
    wf['triggers']=[{'id':'morning','kind':'scheduled','enabled':True,'schedule':{'kind':'once','date':'2026-09-21','time':'07:00'},'timezone':'UTC'}]
    svc.save(wf,wf['id']);svc.tick(utc('2026-09-21T06:59:59Z'));run=svc.tick(utc('2026-09-21T07:00Z'))[0]
    response=client.get('/api/workflow-runs/'+run['id']).json()
    assert utc(response['scheduled_at'])==utc('2026-09-21T07:00Z')
    assert response['started_at'] is None
    execute(svc,run);finished=client.get('/api/workflow-runs/'+run['id']).json()
    assert finished['scheduled_at']==response['scheduled_at']
    assert finished['started_at'] is not None and finished['started_at']!=finished['scheduled_at']
    manual=svc.admit(wf['id']);assert manual.get('scheduled_at') is None


def test_pub004_explicit_node_test_records_source_and_never_replays_sql_write(client):
    admin(client)
    from taskconsole.workflows import WorkflowService
    from taskconsole.workflows_sql import create_connection,execute_sql
    svc=WorkflowService(client.app.state.store)
    conn=create_connection(svc.store,{'name':'Synthetic upstream write','dialect':'sqlite','config':{'synthetic':True},'write_enabled':True})
    sql={'id':'write','name':'Upstream counter','kind':'sql','source':"UPDATE orders SET amount='999' WHERE order_id='A001'",'inputs':{},'config':{'dialect':'sqlite','connection_id':conn['id'],'mode':'write'}}
    consumer={'id':'consumer','kind':'python','source':'def main(inputs): return inputs','inputs':{},'config':{}}
    wf=svc.save({'name':'Node isolation','nodes':[sql,consumer],'edges':[{'source':'write','target':'consumer'}]})
    response=client.post('/api/workflows/'+wf['id']+'/nodes/consumer/test',json={'inputs':{'amount':'10.50','flag':False}})
    assert response.status_code==202,response.text
    run=svc.get_run(response.json()['id']);assert set(run['nodes'])=={'consumer'}
    assert run['test_input_source']=={'kind':'explicit'}
    assert response.json()['test_input_source']=={'kind':'explicit'}
    result=execute(svc,run);assert result['nodes']['consumer']['output']['data']=={'amount':'10.50','flag':False}
    query={**sql,'source':"SELECT amount FROM orders WHERE order_id='A001'",'config':{**sql['config'],'mode':'query'}}
    assert execute_sql(svc.store,query,{},wf['id'])['output']['data']['rows']==[{'amount':'10.50'}]
    historical=client.post('/api/workflows/'+wf['id']+'/nodes/consumer/test',json={'sample_run_id':run['id'],'sample_node_id':'consumer'})
    assert historical.status_code==202,historical.text
    source=historical.json()['test_input_source']
    assert source=={'kind':'historical_sample','run_id':run['id'],'node_id':'consumer','version_id':None,'produced_at':result['nodes']['consumer']['finished_at']}
    assert execute(svc,svc.get_run(historical.json()['id']))['nodes']['consumer']['inputs']=={'amount':'10.50','flag':False}
    assert execute_sql(svc.store,query,{},wf['id'])['output']['data']['rows']==[{'amount':'10.50'}]


def active_graph(tmp_path,params):
    graph=simple_graph();graph['params']=params
    graph['nodes'][0]['inputs']={'position':{'source':'parameter','path':'position'}}
    graph['nodes'][0]['source']='''from pathlib import Path
import time
def main(inputs):
 marker=Path(%r)
 with marker.open('a') as stream: stream.write(str(inputs['position'])+'\\n')
 if inputs['position']==0:
  while not Path(%r).exists(): time.sleep(.02)
 return {'x':inputs['position']}
'''%(str(tmp_path/'effects'),str(tmp_path/'release'))
    return graph


def wait_running(svc,run,tmp_path):
    svc.submit_node(run['id'],'a');deadline=time.monotonic()+5
    while time.monotonic()<deadline:
        if svc.get_run(run['id'])['nodes']['a']['status']=='running' and (tmp_path/'effects').exists():return
        time.sleep(.01)
    raise AssertionError('Actual node process never reached its effect marker')


def test_sch024_running_process_plus_two_waiters_is_bounded_fifo(tmp_path,monkeypatch):
    from taskconsole import workflows_n8n
    from taskconsole.store import stamp
    svc=service(tmp_path);wf=svc.save(active_graph(tmp_path,{'position':0}));svc.publish(wf['id'])
    wf['triggers']=[{'id':'clock','kind':'scheduled','enabled':True,'overlap':'queue','queue_limit':2,'schedule':{'kind':'interval','every':1,'anchor':'2026-09-21T07:00Z'},'timezone':'UTC','params':{'position':0}}]
    svc.save(wf,wf['id']);svc.tick(utc('2026-09-21T06:59:59Z'));first=svc.tick(utc('2026-09-21T07:00Z'))[0]
    try:
        wait_running(svc,first,tmp_path);queued=[]
        for minute in [1,2,3]:
            wf['triggers'][0]['params']={'position':minute};svc.save(wf,wf['id'])
            due=utc('2026-09-21T07:00Z')+timedelta(minutes=minute)
            svc.tick(due-timedelta(seconds=1));admitted=svc.tick(due)
            assert len(admitted)==(1 if minute<3 else 0);queued.extend(admitted)
        assert svc.get_run(first['id'])['status']=='running'
        assert [svc.get_run(r['id'])['status'] for r in queued]==['queued','queued']
        assert workflows_n8n.dispatch_pending(svc)==[]
        assert (tmp_path/'effects').read_text()=='0\n'
        assert [x['reason'] for x in rows(svc,'workflow_occurrence') if x['status']=='skipped']==['overlap_skipped']
        (tmp_path/'release').touch();svc._threads[first['id']+':a'].join(5);execute(svc,first)
        def runner(service,rid):
            execute(service,service.get_run(rid))
            with service.store.transaction() as tx:
                record=tx.get('workflow_run',rid);record['adapter_finished_at']=stamp();tx.put('workflow_run',record)
        monkeypatch.setattr(workflows_n8n,'execute_graph',runner)
        for run in queued:
            assert workflows_n8n.dispatch_pending(svc)==[run['id']]
            svc._threads[run['id']].join(5);assert svc.get_run(run['id'])['status']=='succeeded'
        assert (tmp_path/'effects').read_text()=='0\n1\n2\n'
        assert [svc.get_run(r['id'])['params'] for r in queued]==[{'position':1},{'position':2}]
    finally:
        (tmp_path/'release').touch()
        if svc.get_run(first['id'])['status'] in {'queued','running','cancelling'}:svc.cancel(first['id'])


def test_sch034_schedule_edit_while_process_running_preserves_snapshot(tmp_path):
    svc=service(tmp_path);wf=svc.save(active_graph(tmp_path,{'position':0}));svc.publish(wf['id'])
    wf['triggers']=[{'id':'clock','kind':'scheduled','enabled':True,'schedule':{'kind':'interval','every':1,'anchor':'2026-09-21T07:00Z'},'timezone':'UTC','params':{'position':0}}]
    svc.save(wf,wf['id']);svc.tick(utc('2026-09-21T06:59:59Z'));old=svc.tick(utc('2026-09-21T07:00Z'))[0]
    try:
        wait_running(svc,old,tmp_path);snapshot=copy.deepcopy(svc.get_run(old['id'])['snapshot'])
        wf['triggers'][0]['schedule']={'kind':'daily','time':'08:00'};wf['triggers'][0]['params']={'position':9};svc.save(wf,wf['id'])
        current=svc.get_run(old['id']);assert current['status']=='running' and current['snapshot']==snapshot and current['params']=={'position':0}
        (tmp_path/'release').touch();svc._threads[old['id']+':a'].join(5);execute(svc,old)
        svc.tick(utc('2026-09-21T07:00:59Z'));assert svc.tick(utc('2026-09-21T07:01Z'))==[]
        svc.tick(utc('2026-09-21T07:59:59Z'));new=svc.tick(utc('2026-09-21T08:00Z'));assert len(new)==1
        assert new[0]['params']=={'position':9} and new[0]['version_id']==old['version_id'];execute(svc,new[0])
        assert (tmp_path/'effects').read_text()=='0\n9\n'
    finally:
        (tmp_path/'release').touch()
        if svc.get_run(old['id'])['status'] in {'queued','running','cancelling'}:svc.cancel(old['id'])
