"""User-visible schedule gaps and safe explicit run decisions in real Chromium."""
import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from test_acceptance_browser import browser, engine_url, isolated_url, pytestmark
from test_acceptance_browser_mapping import HELPERS, durable_runs


def test_browser_pending_admission_is_visible_and_retry_preserves_identity(isolated_url):
    browser(isolated_url,r'''
 const w=await seed({nodes:[node('a')],edges:[]});await request('/api/workflows/'+w.id+'/publish','POST',{});await open(w);
 let release,arrived;const held=new Promise(resolve=>release=resolve),requested=new Promise(resolve=>arrived=resolve),bodies=[];let first=true;
 await page.route('**/api/workflows/'+w.id+'/run',async route=>{bodies.push(route.request().postDataJSON());arrived();if(first){first=false;const r=await route.fetch();assert.equal(r.status(),202);await held;await route.abort('failed');}else await route.continue();});
 await page.getByRole('button',{name:'Run published',exact:true}).click();
 await page.getByRole('status').filter({hasText:'Waiting for run confirmation'}).waitFor();
 assert.equal(await page.getByRole('button',{name:'Run published',exact:true}).isDisabled(),true);
 assert.equal(await page.getByRole('button',{name:'Test workflow',exact:true}).isDisabled(),true);
 await requested;assert.equal(bodies.length,1);release();
 await page.getByRole('button',{name:'Recover pending run',exact:true}).waitFor();
 const firstRun=(await request('/api/workflow-runs'))[0];await page.reload();
 await page.getByRole('button',{name:'Recover pending run',exact:true}).waitFor();
 const response=page.waitForResponse(r=>r.url().endsWith('/run')&&r.request().method()==='POST');await page.getByRole('button',{name:'Recover pending run',exact:true}).click();await response;
 assert.equal(bodies.length,2);assert.deepEqual(bodies[0],bodies[1]);const all=await request('/api/workflow-runs');assert.equal(all.length,1);assert.equal(all[0].id,firstRun.id);
 ''')


def test_browser_storage_disabled_refresh_recovers_one_actual_effect(engine_url,tmp_path):
    effect=tmp_path/'pending-effect'
    source=f'from pathlib import Path\ndef main(inputs):\n with Path({str(effect)!r}).open("a") as f:f.write("once\\n")\n return {{"complete":True}}'
    browser(engine_url,HELPERS+'const source='+json.dumps(source)+';'+r'''
 await context.addInitScript(()=>{for(const key of ['getItem','setItem','removeItem'])Object.defineProperty(window.sessionStorage,key,{value:()=>{throw new DOMException('Disabled','SecurityError');}});});
 const w=await seed({nodes:[node('a',{source})],edges:[]});await request('/api/workflows/'+w.id+'/publish','POST',{});await open(w);
 let release,arrived;const held=new Promise(resolve=>release=resolve),requested=new Promise(resolve=>arrived=resolve),bodies=[];let first=true;
 await page.route('**/api/workflows/'+w.id+'/run',async route=>{bodies.push(route.request().postDataJSON());arrived();if(first){first=false;const r=await route.fetch();assert.equal(r.status(),202);await held;await route.abort('failed');}else await route.continue();});
 await page.getByRole('button',{name:'Run published',exact:true}).dblclick();await requested;
 assert.equal(await page.getByRole('button',{name:'Run published',exact:true}).isDisabled(),true);assert.equal(bodies.length,1);release();
 await page.getByRole('button',{name:'Recover pending run',exact:true}).waitFor();const original=(await request('/api/workflow-runs'))[0];await page.reload();
 await page.getByRole('button',{name:'Recover pending run',exact:true}).waitFor();const response=page.waitForResponse(r=>r.url().endsWith('/run')&&r.request().method()==='POST');await page.getByRole('button',{name:'Recover pending run',exact:true}).click();const recovered=await(await response).json();assert.equal(recovered.id,original.id);assert.deepEqual(bodies,[bodies[0],bodies[0]]);
 const done=await waitRun(recovered);assert.equal(done.status,'succeeded',done.error);assert.equal(done.nodes.a.attempts.length,1);assert.equal((await request('/api/workflow-runs')).length,1);
 ''')
    assert effect.read_text().splitlines()==['once']


def test_browser_failed_effect_warns_before_new_run_in_both_languages(engine_url,tmp_path):
    counter=tmp_path/'effect-ledger'
    source=f'from pathlib import Path\nimport os\ndef main(inputs):\n with Path({str(counter)!r}).open("a") as f:f.write("effect\\n");f.flush();os.fsync(f.fileno())\n raise RuntimeError("Acknowledgement lost after external effect")'
    browser(engine_url,HELPERS+'const source='+json.dumps(source)+';'+r'''
 const w=await seed({nodes:[node('effect',{source})],edges:[]});await request('/api/workflows/'+w.id+'/publish','POST',{});
 const run=await waitRun(await request('/api/workflows/'+w.id+'/run','POST',{idempotency_key:'first-effect'}));assert.equal(run.status,'failed');assert.equal(run.side_effects_uncertain,true);await open(w);
 const messages=[],dismiss=async dialog=>{messages.push(dialog.message());await dialog.dismiss();};page.on('dialog',dismiss);
 await page.getByRole('button',{name:'Run published',exact:true}).click();await page.waitForTimeout(150);assert.equal(messages.length,1);assert.match(messages[0],/external side effects/i);assert.equal((await request('/api/workflow-runs')).length,1);
 await page.locator('[data-locale="zh-CN"]').click();await page.getByRole('button',{name:'运行已发布版本',exact:true}).click();await page.waitForTimeout(150);assert.equal(messages.length,2);assert.ok(messages[1].includes('外部'));assert.equal((await request('/api/workflow-runs')).length,1);
 await page.locator('[data-locale="en"]').click();page.off('dialog',dismiss);page.once('dialog',async dialog=>{assert.match(dialog.message(),/external side effects/);await dialog.accept();});const admitted=page.waitForResponse(r=>r.url().endsWith('/run')&&r.request().method()==='POST');await page.getByRole('button',{name:'Run published',exact:true}).click();const second=await waitRun(await(await admitted).json());assert.notEqual(second.id,run.id);assert.equal(second.version_id,run.version_id);assert.equal(second.status,'failed');assert.equal((await request('/api/workflow-runs')).length,2);assert.deepEqual((await request('/api/workflow-runs/'+run.id)).nodes,run.nodes);
 ''')
    assert counter.read_text().splitlines()==['effect','effect']
    assert len(durable_runs(tmp_path))==2


def test_browser_schedule_history_shows_offline_gap_and_exact_instants(isolated_url,tmp_path):
    from taskconsole.store import Store
    from taskconsole.workflows import WorkflowService
    store=Store(tmp_path,'sqlite:///'+str(tmp_path/'browser.sqlite'));svc=WorkflowService(store)
    wf=svc.save({'name':'History fixture','timezone':'America/Chicago','nodes':[{'id':'a','kind':'python','source':'def main(inputs): return {}','inputs':{},'config':{}}],'edges':[]})
    svc.publish(wf['id']);start=datetime(2026,9,21,12,0,tzinfo=timezone.utc)
    wf=svc.save({**wf,'enabled':True,'schedule':{'kind':'interval','every':1,'anchor':start.isoformat()}},wf['id'])
    svc.tick(start);svc.tick(start+timedelta(minutes=5))
    browser(isolated_url,'const id='+json.dumps(wf['id'])+';'+r'''
 const w=await request('/api/workflows/'+id),expected=await request('/api/workflows/'+id+'/schedule-history');assert.ok(expected.items.some(i=>i.kind==='gap'));await open(w);await page.getByRole('button',{name:'Runs',exact:true}).click();
 const section=page.getByRole('region',{name:'Schedule history',exact:true});await section.waitFor();assert.ok((await section.textContent()).includes('America/Chicago'));
 for(const entry of expected.items){const row=section.locator('[data-history-id="'+entry.id+'"]');await row.waitFor();if(entry.from)assert.equal(await row.locator('time').first().getAttribute('datetime'),entry.from);if(entry.occurrence)assert.equal(await row.locator('time').first().getAttribute('datetime'),entry.occurrence);}
 assert.ok((await section.textContent()).includes('offline'));await page.locator('[data-locale="zh-CN"]').click();await page.getByRole('region',{name:'计划执行记录',exact:true}).waitFor();await page.reload();await page.getByRole('button',{name:'运行记录',exact:true}).click();await page.getByRole('region',{name:'计划执行记录',exact:true}).waitFor();assert.deepEqual((await request('/api/workflows/'+id+'/schedule-history')).items,expected.items);const admitted=expected.items.find(i=>i.run_id);assert.ok(admitted);await page.locator('[data-history-id="'+admitted.id+'"]').getByRole('button',{name:'查看运行',exact:true}).click();await page.waitForURL('**/workflow-runs/'+admitted.run_id);
 ''')
    store.engine.dispose()
