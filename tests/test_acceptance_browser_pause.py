"""MAC-BG-07: browser pause controls with a real isolated supervisor/n8n.

Production scheduler ticks use explicit future due instants so this does not
change the OS clock or wait for minute boundaries. Power assertions are disabled
by the existing supervisor fixture; this is not physical assertion evidence.
"""
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import threading
import traceback

import pytest

from taskconsole.local import read_json, status, write_json
from test_acceptance_browser import browser
from test_acceptance_engine_recovery import execution_count
from test_acceptance_local_recovery import supervisor, until, alive, finished, healthy_same

pytestmark=pytest.mark.skipif(
    os.environ.get('SLEEP_IN_BROWSER_TEST')!='1' or not os.environ.get('SLEEP_IN_TEST_N8N_COMMAND'),
    reason='BLOCKED_ENV: isolated Chromium and actual supervisor/n8n required')


def test_mac_bg07_browser_pause_one_then_global_pause_preserves_active_work_and_service(supervisor):
    s=supervisor
    anchor=(datetime.now(timezone.utc)+timedelta(days=1)).replace(second=0,microsecond=0)
    gate=s.state/'active-release'
    ledgers={key:s.state/(key+'-effects') for key in ('a','b')}
    workflows={}
    for key in ('a','b'):
        source=('from pathlib import Path\nimport os,time\ndef main(inputs):\n'
                f' with Path({str(ledgers[key])!r}).open("a") as f: f.write("started\\n");f.flush();os.fsync(f.fileno())\n'
                +(f' while not Path({str(gate)!r}).exists(): time.sleep(.02)\n' if key=='a' else '')
                +f' with Path({str(ledgers[key])!r}).open("a") as f: f.write("finished\\n");f.flush();os.fsync(f.fileno())\n'
                +f' return {{"workflow":{key!r},"finished":True}}\n')
        wf=s.svc.save({'name':'Pause fixture '+key,'timeout':180,'nodes':[
            {'id':'effect','kind':'python','source':source,'inputs':{},'config':{}}],'edges':[]})
        s.svc.publish(wf['id'])
        workflows[key]=s.svc.save({**wf,'enabled':True,'schedule':{
            'kind':'interval','every':1,'unit':'minutes','anchor':anchor.isoformat()}},wf['id'])
    ids={key:value['id'] for key,value in workflows.items()}
    ids_set=set(ids.values())
    preference=(s.state/'local-preferences.json').read_bytes()
    configuration=(s.state/'local-config.json').read_bytes()
    stop=threading.Event();controller=None
    phases={}

    def snapshot():
        with s.store.transaction() as tx:
            occurrences=[row for row in tx.all('workflow_occurrence') if row['workflow_id'] in ids_set]
            runs=[row for row in tx.all('workflow_run') if row['workflow_id'] in ids_set]
        return {'occurrences':occurrences,'runs':runs}

    def assert_service():
        healthy_same(s)
        assert alive(s.initial['pid'])
        assert (s.state/'local-preferences.json').read_bytes()==preference
        assert (s.state/'local-config.json').read_bytes()==configuration
        assert status(s.state)['assertion'] is False
        assert not (s.state/'local-stop-request.json').exists()

    try:
        assert s.svc.tick(anchor-timedelta(seconds=1))==[]
        initial=s.svc.tick(anchor)
        assert {run['workflow_id'] for run in initial}==ids_set
        run_ids={key:next(run['id'] for run in initial if run['workflow_id']==wid) for key,wid in ids.items()}
        until(lambda:ledgers['a'].exists())
        assert finished(s,run_ids['b'])['status']=='succeeded'
        assert s.svc.get_run(run_ids['a'])['status']=='running'
        before=snapshot()
        assert len(before['runs'])==len(before['occurrences'])==2
        assert all(row['status']=='admitted' and row['occurrence']==anchor.isoformat() for row in before['occurrences'])
        assert_service()

        def control():
            while not stop.wait(.05):
                for phase in ('one','finish','all'):
                    if phase in phases or not (s.state/('pause-'+phase+'-request')).exists():
                        continue
                    try:
                        if phase=='one':
                            assert s.client.get('/api/workflows/'+ids['a']).json()['enabled'] is False
                            assert s.client.get('/api/workflows/'+ids['b']).json()['enabled'] is True
                            admitted=s.svc.tick(anchor+timedelta(minutes=1))
                            assert len(admitted)==1 and admitted[0]['workflow_id']==ids['b']
                            assert s.svc.tick(anchor+timedelta(minutes=1))==[]
                            run_ids['b_second']=admitted[0]['id']
                            assert finished(s,run_ids['b_second'])['status']=='succeeded'
                            assert s.svc.get_run(run_ids['a'])['status']=='running'
                            value=snapshot()
                            assert [sum(r['workflow_id']==ids[k] for r in value['occurrences']) for k in ('a','b')]==[1,2]
                            assert len(value['runs'])==3
                            assert ledgers['a'].read_text().splitlines()==['started']
                            assert ledgers['b'].read_text().splitlines()==['started','finished']*2
                        elif phase=='finish':
                            assert s.client.get('/api/workflow-maintenance').json()['paused'] is True
                            assert s.svc.get_run(run_ids['a'])['status']=='running'
                            assert ledgers['a'].read_text().splitlines()==['started']
                            gate.touch()
                            active=finished(s,run_ids['a'])
                            assert active['status']=='succeeded' and active['n8n_execution_id']
                            assert active['nodes']['effect']['output']['data']=={'workflow':'a','finished':True}
                            assert ledgers['a'].read_text().splitlines()==['started','finished']
                            value=snapshot()
                            assert len(value['runs'])==3
                        else:
                            assert all(s.client.get('/api/workflows/'+wid).json()['enabled'] is True for wid in ids.values())
                            assert s.client.get('/api/workflow-maintenance').json()['paused'] is True
                            assert s.svc.tick(anchor+timedelta(minutes=2))==[]
                            assert s.svc.tick(anchor+timedelta(minutes=2))==[]
                            value=snapshot()
                            assert [sum(r['workflow_id']==ids[k] for r in value['occurrences']) for k in ('a','b')]==[2,3]
                            assert len(value['runs'])==3
                            admitted=[row for row in value['occurrences'] if row.get('run_id')]
                            pending=[row for row in value['occurrences'] if not row.get('run_id')]
                            assert len(admitted)==3 and {row['run_id'] for row in admitted}==set(run_ids.values())
                            assert len(pending)==2 and {row['workflow_id'] for row in pending}==ids_set
                            assert all(row['occurrence']==(anchor+timedelta(minutes=2)).isoformat() and row['status'] in {'pending','admitting'} for row in pending)
                            assert ledgers['a'].read_text().splitlines()==['started','finished']
                            assert ledgers['b'].read_text().splitlines()==['started','finished']*2
                        assert_service()
                        phases[phase]=value
                        write_json(s.state/('pause-'+phase+'-result.json'),{'ok':True,'run_ids':run_ids,
                            'occurrences':[{'workflow_id':r['workflow_id'],'occurrence':r['occurrence'],
                                'run_id':r.get('run_id'),'status':r['status']} for r in value['occurrences']]})
                    except BaseException:
                        phases[phase]={'error':traceback.format_exc()}
                        write_json(s.state/('pause-'+phase+'-result.json'),phases[phase])
        controller=threading.Thread(target=control,daemon=True);controller.start()
        browser(s.initial['url'],'const fixture='+json.dumps({'ids':ids,'run_ids':run_ids,'state':str(s.state)})+';'+r"""
 const fs=await import('node:fs');
 const phase=async name=>{fs.writeFileSync(fixture.state+'/pause-'+name+'-request','requested');const path=fixture.state+'/pause-'+name+'-result.json',deadline=Date.now()+70000;while(!fs.existsSync(path)&&Date.now()<deadline)await page.waitForTimeout(100);assert.ok(fs.existsSync(path),'controller phase '+name+' did not finish');const value=JSON.parse(fs.readFileSync(path,'utf8'));assert.equal(value.ok,true,value.error);return value;};
 const configure=async enabled=>{await open({id:fixture.ids.a});await page.getByRole('button',{name:'Triggers',exact:true}).click();const control=page.getByLabel('Schedule enabled',{exact:true});assert.equal(await control.isDisabled(),false);await control.setChecked(enabled);const response=page.waitForResponse(r=>r.url().endsWith('/api/workflows/'+fixture.ids.a)&&r.request().method()==='PUT');await page.getByRole('button',{name:'Save draft',exact:true}).last().click();assert.equal((await response).status(),200);assert.equal((await request('/api/workflows/'+fixture.ids.a)).enabled,enabled);};
 assert.equal((await request('/api/workflow-runs/'+fixture.run_ids.a)).status,'running');await configure(false);const one=await phase('one');assert.equal(one.occurrences.filter(x=>x.workflow_id===fixture.ids.a).length,1);assert.equal(one.occurrences.filter(x=>x.workflow_id===fixture.ids.b).length,2);await page.reload();await page.getByRole('button',{name:'Triggers',exact:true}).click();assert.equal(await page.getByLabel('Schedule enabled',{exact:true}).isChecked(),false);
 await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByText('Advanced settings',{exact:true}).click();await page.getByRole('button',{name:'Workspace operations',exact:true}).click();await page.getByText('Retention and admission',{exact:true}).click();const response=page.waitForResponse(r=>r.url().endsWith('/api/workflow-maintenance/pause'));await page.getByRole('button',{name:'Pause new admission',exact:true}).click();assert.deepEqual(await(await response).json(),{paused:true});await page.getByText('Retention and admission',{exact:true}).click();await page.getByRole('button',{name:'Resume admission',exact:true}).waitFor();await phase('finish');
 // A scheduled trigger can remain enabled while the independent global gate
 // blocks admission. This makes the final due instant apply to both workflows.
 await configure(true);const all=await phase('all');assert.equal(all.occurrences.filter(x=>!x.run_id).length,2);assert.equal((await request('/api/workflow-maintenance')).paused,true);await page.goto(origin+'/workflow-runs/'+fixture.run_ids.a);await page.locator('.wf-run-summary').getByText('Succeeded',{exact:true}).waitFor();assert.equal((await request('/api/workflow-runs/'+fixture.run_ids.a)).nodes.effect.attempts.length,1);await page.locator('#scheduler-health').getByText('Ready',{exact:true}).waitFor();const aHistory=await request('/api/workflows/'+fixture.ids.a+'/schedule-history'),bHistory=await request('/api/workflows/'+fixture.ids.b+'/schedule-history');assert.equal(aHistory.items.filter(x=>x.kind==='occurrence').length,2);assert.equal(bHistory.items.filter(x=>x.kind==='occurrence').length,3);
 """)
        assert set(phases)=={'one','finish','all'} and all('error' not in value for value in phases.values())
        assert_service()
        final=snapshot()
        assert len(final['runs'])==3
        assert {run['id'] for run in final['runs']}==set(run_ids.values())
        for run in final['runs']:
            assert run['status']=='succeeded' and run['n8n_execution_id'] and run['adapter_finished_at']
            assert run['engine']=='n8n' and len(run['nodes']['effect']['attempts'])==1
            assert execution_count(s,run['id'])==1
    finally:
        gate.touch();stop.set()
        if controller:
            controller.join(75)
            assert not controller.is_alive()
