"""Real Chromium+n8n against disposable PostgreSQL; outage stops only our TCP relay."""
import json
import os
import select
import socket
import socketserver
import threading
import time
import uuid

import pytest

from test_acceptance_browser import browser, engine_url, isolated_url
from test_acceptance_browser_mapping import HELPERS, durable_runs
from test_workflow_external_sql import fixture_config


EXTERNAL_BROWSER = pytest.mark.skipif(
    os.environ.get('SLEEP_IN_EXTERNAL_SQL_TESTS') != '1'
    or os.environ.get('SLEEP_IN_BROWSER_TEST') != '1'
    or 'postgresql' not in os.environ.get('SLEEP_IN_TEST_SQL_DIALECTS', 'postgresql,mysql,oracle').split(','),
    reason='BLOCKED_ENV: actual PostgreSQL+n8n+Chromium fixture required')

SETTLED_ENGINE = r"""
const waitAdapter=async run=>{
 const deadline=Date.now()+15000;let current=run;
 while(!current.adapter_finished_at&&Date.now()<deadline){
  await page.waitForTimeout(100);current=await request('/api/workflow-runs/'+run.id);
 }
 assert.ok(current.adapter_finished_at,'adapter completion was not persisted');
 assert.ok(current.n8n_execution_id,'real n8n execution identity missing');
 return current;
};
"""


class Relay:
    """Transparent owned TCP path; forwards real database bytes without interpreting them."""
    def __init__(self, target):
        self.closing = threading.Event()
        closing = self.closing
        class Handler(socketserver.BaseRequestHandler):
            def handle(handler):
                try:
                    with socket.create_connection(target, timeout=3) as remote:
                        remote.settimeout(None)
                        sockets = [handler.request, remote]
                        while not closing.is_set():
                            ready, _, _ = select.select(sockets, [], [], .1)
                            for source in ready:
                                data = source.recv(65536)
                                if not data:
                                    return
                                destination = remote if source is handler.request else handler.request
                                destination.sendall(data)
                except OSError:
                    return
        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True
        self.server = Server(('127.0.0.1', 0), Handler)
        self.port = self.server.server_address[1]
        self.thread = threading.Thread(target=self.server.serve_forever, kwargs={'poll_interval': .05}, daemon=True)
        self.thread.start()

    def close(self):
        if self.closing.is_set():
            return
        self.closing.set()
        self.server.shutdown(); self.server.server_close(); self.thread.join(5)
        assert not self.thread.is_alive()


def test_owned_relay_forwards_real_bytes_and_closes_only_its_listener():
    """Transport-fixture check only: this is not external database acceptance evidence."""
    class Echo(socketserver.BaseRequestHandler):
        def handle(self):
            self.request.sendall(self.request.recv(1024))
    target = socketserver.ThreadingTCPServer(('127.0.0.1', 0), Echo)
    thread = threading.Thread(target=target.serve_forever, daemon=True); thread.start()
    relay = Relay(target.server_address)
    try:
        with socket.create_connection(('127.0.0.1', relay.port), timeout=2) as conn:
            conn.sendall(b'fixture-transport-only'); assert conn.recv(1024) == b'fixture-transport-only'
        relay.close()
        with pytest.raises(OSError):
            socket.create_connection(('127.0.0.1', relay.port), timeout=1)
        with socket.create_connection(target.server_address, timeout=2) as conn:
            conn.sendall(b'independent-listener'); assert conn.recv(1024) == b'independent-listener'
    finally:
        relay.close(); target.shutdown(); target.server_close(); thread.join(5)


@pytest.fixture
def postgres_orders(tmp_path):
    import psycopg
    config = fixture_config('postgresql')
    table = 'si_browser_' + uuid.uuid4().hex[:12]
    db = psycopg.connect(**config)
    created = False; relay = None
    stop = tmp_path / 'stop-owned-relay'
    stopped = tmp_path / 'owned-relay-stopped'
    controller_done = threading.Event(); controller = None
    try:
        with db.cursor() as cur:
            cur.execute(f'CREATE TABLE {table}(order_id TEXT PRIMARY KEY, amount TEXT NOT NULL, region TEXT)')
            created = True
            cur.executemany(f'INSERT INTO {table} VALUES(%s,%s,%s)',
                [('A001', '10.50', '华东'), ('A002', '20.25', None), ('A003', '0.00', '西部')])
        db.commit()
        relay = Relay((config.get('hostaddr', config.get('host', '127.0.0.1')), int(config['port'])))
        def control():
            while not controller_done.wait(.05):
                if stop.exists():
                    relay.close(); stopped.write_text('own relay closed'); return
        controller = threading.Thread(target=control, daemon=True); controller.start()
        yield {'config': {**config, 'host': '127.0.0.1', 'hostaddr': '127.0.0.1', 'port': relay.port,
                          'connect_timeout': 2, 'sslmode': 'disable'},
               'table': table, 'stop': str(stop), 'stopped': str(stopped)}
    finally:
        controller_done.set()
        if controller:
            controller.join(5)
        if relay:
            relay.close()
        try:
            db.rollback()
            if created:
                with db.cursor() as cur:
                    cur.execute('DROP TABLE ' + table)
                db.commit()
        finally:
            db.close()


@EXTERNAL_BROWSER
def test_ui_r11_template_rebinds_explicitly_to_actual_postgresql_without_mutating_other_instance(postgres_orders, engine_url, tmp_path):
    browser(engine_url, HELPERS + SETTLED_ENGINE + 'const fixture=' + json.dumps(postgres_orders) + ';' + r"""
 const catalog=(await request('/api/workflow-templates'))[0],instances=[];
 for(let i=0;i<2;i++){
  await page.goto(origin+'/templates');
  const pending=page.waitForResponse(r=>r.url().endsWith('/api/workflows')&&r.request().method()==='POST');
  await page.getByRole('button',{name:'Open visual editor',exact:true}).click();
  instances.push(await(await pending).json());
 }
 assert.notEqual(instances[0].id,instances[1].id);
 const untouched=await request('/api/workflows/'+instances[1].id);
 await open(instances[0]);const baseline=await testRun(instances[0]);
 assert.equal(baseline.status,'succeeded',baseline.error);
 assert.deepEqual(baseline.nodes.summary.output.data.summary,{count:3,total:'30.75'});
 const c=await request('/api/connections','POST',{name:'Explicit PostgreSQL F-ORDERS',dialect:'postgresql',config:fixture.config});
 await open(instances[0]);await selectNode('orders');
 await page.getByLabel('Database dialect',{exact:true}).selectOption('postgresql');
 assert.equal(await page.getByLabel('Connection',{exact:true}).inputValue(),'');
 await page.getByLabel('Connection',{exact:true}).selectOption(c.id);
 await page.getByLabel('Source code',{exact:true}).fill('SELECT order_id,amount,region FROM '+fixture.table+' ORDER BY order_id');
 const saved=await save(instances[0]);
 assert.equal(saved.nodes.find(n=>n.id==='orders').config.connection_id,c.id);
 assert.equal(saved.nodes.find(n=>n.id==='orders').config.dialect,'postgresql');
 const published=page.waitForResponse(r=>r.url().endsWith('/publish'));
 await page.getByRole('button',{name:'Publish',exact:true}).click();assert.equal((await published).status(),200);
 const pending=page.waitForResponse(r=>r.url().endsWith('/run'));
 await page.getByRole('button',{name:'Run published',exact:true}).click();
 const run=await waitRun(await(await pending).json());assert.equal(run.status,'succeeded',run.error);
 assert.deepEqual(run.nodes.orders.output.data.rows,[{order_id:'A001',amount:'10.50',region:'华东'},{order_id:'A002',amount:'20.25',region:null},{order_id:'A003',amount:'0.00',region:'西部'}]);
 assert.deepEqual(run.nodes.summary.output.data.summary,{count:3,total:'30.75'});
 assert.equal(run.nodes.report.output.data.message,'3 orders • 30.75');
 assert.ok(Object.values(run.nodes).every(n=>n.attempts.length===1));
 await page.goto(origin+'/workflow-runs/'+run.id);
 const reportDownload=page.waitForEvent('download');await page.getByRole('link',{name:'report.txt',exact:true}).click();
 let report='';for await(const part of await(await reportDownload).createReadStream())report+=part.toString();
 assert.equal(report,'3 orders • 30.75\n');
 assert.deepEqual(await request('/api/workflows/'+instances[1].id),untouched);
 assert.deepEqual((await request('/api/workflow-templates'))[0],catalog);
 await open(instances[0]);await page.getByRole('button',{name:'Settings',exact:true}).click();
 await page.getByText('Advanced settings',{exact:true}).click();
 const downloaded=page.waitForEvent('download');await page.getByRole('button',{name:'Export portable template',exact:true}).click();
 let exported='';for await(const part of await(await downloaded).createReadStream())exported+=part.toString();
 for(const hidden of [fixture.config.password,'127.0.0.1',fixture.config.user,c.id,run.id,baseline.id,'encrypted_config'])assert.equal(exported.includes(hidden),false,hidden);
 assert.equal(JSON.parse(exported).workflow.nodes.find(n=>n.id==='orders').config.connection_id,undefined);
 await waitAdapter(baseline);await waitAdapter(run);
 """)
    runs = durable_runs(tmp_path)
    assert len(runs) == 2 and all(run['status'] == 'succeeded' and run['n8n_execution_id'] for run in runs)


@EXTERNAL_BROWSER
def test_ui_r12_actual_connection_test_timestamp_does_not_hide_later_network_failure(postgres_orders, engine_url, tmp_path):
    browser(engine_url, HELPERS + SETTLED_ENGINE + 'const fixture=' + json.dumps(postgres_orders) + ';' + r"""
 const fs=await import('node:fs');
 const c=await request('/api/connections','POST',{name:'Disconnect only owned relay',dialect:'postgresql',config:fixture.config});
 await page.goto(origin+'/connections');
 const card=page.locator('article.wf-resource').filter({hasText:c.name});
 const start=Date.now(),pending=page.waitForResponse(r=>r.url().endsWith('/api/connections/'+c.id+'/test'));
 await card.getByRole('button',{name:'Test connection',exact:true}).click();
 const checked=await pending;assert.equal(checked.status(),200);assert.equal((await checked.json()).ok,true);
 await page.locator('.toast').filter({hasText:'Read-only connection test succeeded'}).waitFor();
 const verified=(await request('/api/connections')).find(x=>x.id===c.id);
 assert.equal(verified.status,'verified');assert.ok(Date.parse(verified.last_test_at)>=start-1000);
 assert.equal(JSON.stringify(verified).includes(fixture.config.password),false);
 assert.equal(verified.config,undefined);assert.equal(verified.encrypted_config,undefined);
 const {id,...template}=(await request('/api/workflow-templates'))[0];
 const sql=template.nodes.find(n=>n.id==='orders');
 sql.config={dialect:'postgresql',mode:'query',connection_id:c.id,timeout:3};
 sql.source='SELECT order_id,amount,region FROM '+fixture.table+' ORDER BY order_id';
 const w=await seed(template);await request('/api/workflows/'+w.id+'/publish','POST',{});
 fs.writeFileSync(fixture.stop,'stop our test relay');
 const until=Date.now()+10000;while(!fs.existsSync(fixture.stopped)&&Date.now()<until)await page.waitForTimeout(50);
 assert.ok(fs.existsSync(fixture.stopped));
 const failure=page.waitForResponse(r=>r.url().endsWith('/api/connections/'+c.id+'/test'));
 await card.getByRole('button',{name:'Test connection',exact:true}).click();
 const failedTest=await failure;assert.equal(failedTest.status(),200);assert.equal((await failedTest.json()).ok,false);
 await page.locator('.toast').filter({hasText:'Connection test failed'}).waitFor();
 const failed=(await request('/api/connections')).find(x=>x.id===c.id);
 assert.equal(failed.status,'failed');assert.ok(Date.parse(failed.last_test_at)>Date.parse(verified.last_test_at));
 await open(w);const admitted=page.waitForResponse(r=>r.url().endsWith('/run'));
 await page.getByRole('button',{name:'Run published',exact:true}).click();
 const run=await waitRun(await(await admitted).json());assert.equal(run.status,'failed');
 assert.equal(run.nodes.orders.status,'failed');assert.equal(run.nodes.orders.attempts.length,1);
 assert.equal(run.nodes.orders.inputs && Object.keys(run.nodes.orders.inputs).length,0);
 assert.equal(run.nodes.summary.attempts.length,0);assert.equal(run.nodes.report.attempts.length,0);
 await page.goto(origin+'/workflow-runs/'+run.id);await page.reload();
 await page.locator('.wf-run-summary').getByText('Failed',{exact:true}).waitFor();
 const sqlCard=page.locator('.wf-node-detail').filter({has:page.locator('summary strong').filter({hasText:'Order query'})});
 if(await sqlCard.getAttribute('open')===null)await sqlCard.locator('summary').first().click();
 assert.ok((await sqlCard.textContent()).includes('connection'));
 const stored=await request('/api/workflows/'+w.id),exported=await request('/api/workflows/'+w.id+'/export');
 assert.equal(stored.nodes.find(n=>n.id==='orders').source,sql.source);
 for(const value of [JSON.stringify(stored),JSON.stringify(run),JSON.stringify(exported),JSON.stringify(failed),await page.locator('body').textContent()])assert.equal(value.includes(fixture.config.password),false);
 await waitAdapter(run);
 """)
    runs = durable_runs(tmp_path)
    assert len(runs) == 1 and runs[0]['status'] == 'failed' and runs[0]['n8n_execution_id']
