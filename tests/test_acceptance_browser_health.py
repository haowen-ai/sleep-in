"""Workflow readiness must include the independently reported engine state."""
from datetime import datetime, timezone

from test_acceptance_browser import browser, isolated_url, pytestmark


def test_browser_ready_worker_with_engine_incident_is_degraded_in_both_locales(isolated_url,tmp_path):
    from taskconsole.store import Store
    store=Store(tmp_path,'sqlite:///'+str(tmp_path/'browser.sqlite'))
    try:
        with store.transaction() as tx:
            tx.put('meta',{'id':'workflow_worker','status':'ready','n8n_available':True,
                'last_seen':datetime.now(timezone.utc).isoformat(),'instance_id':'ui-health-fixture',
                'engine_status':'degraded','engine_incident':{'code':'cli_exit','run_id':'fixture-run','instance_id':'ui-health-fixture'},'engine_verification':None})
        browser(isolated_url,r"""
 const boot=await request('/api/bootstrap'),health=await request('/api/workflow-health');assert.equal(boot.workflow_scheduler.status,'ready');assert.equal(health.engine_status,'degraded');assert.equal(await page.locator('html').getAttribute('lang'),'en');const indicator=page.locator('#scheduler-health');await indicator.getByText('Degraded',{exact:true}).waitFor();assert.equal(await indicator.getByText('Ready',{exact:true}).count(),0);assert.match(await indicator.locator('[title]').getAttribute('title'),/execution engine.*incident/i);await page.locator('[data-locale="zh-CN"]').click();await indicator.getByText('服务降级',{exact:true}).waitFor();assert.ok((await indicator.locator('[title]').getAttribute('title')).includes('执行引擎'));await page.locator('[data-locale="en"]').click();await indicator.getByText('Degraded',{exact:true}).waitFor();assert.deepEqual(await request('/api/workflow-runs'),[]);
 // Controlled health responses exercise the UI polling contract only. Actual
 // CLI incident/recovery evidence belongs to test_acceptance_engine_health.py.
 await page.clock.install();let next={...health,engine_status:'verified',engine_incident:null};let bootstrap=boot;await page.route('**/api/workflow-health',route=>route.fulfill({json:next}));await page.route('**/api/bootstrap',route=>route.fulfill({json:bootstrap}));await page.clock.runFor(10001);await indicator.getByText('Ready',{exact:true}).waitFor();next={...health};await page.clock.runFor(10001);await indicator.getByText('Degraded',{exact:true}).waitFor();bootstrap={...boot,workflow_scheduler:{status:'unavailable',reason:'Fixture worker stopped'}};await page.clock.runFor(10001);await indicator.getByText('Unavailable',{exact:true}).waitFor();assert.equal(await indicator.getByText('Degraded',{exact:true}).count(),0);
 await page.unroute('**/api/workflow-health');await page.route('**/api/workflow-health',route=>route.abort());bootstrap=boot;await page.clock.runFor(10001);await indicator.getByText('Unavailable',{exact:true}).waitFor();await page.goto(origin+'/tasks');await page.getByRole('button',{name:'Create task',exact:true}).waitFor();assert.equal(await indicator.getByText('Degraded',{exact:true}).count(),0);assert.equal(await page.locator('html').getAttribute('lang'),'en');
 """)
    finally:
        store.engine.dispose()
