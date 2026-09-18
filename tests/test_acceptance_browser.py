"""Real Chromium against a fresh synthetic loopback application.

Enable with SLEEP_IN_BROWSER_TEST=1 and optionally PLAYWRIGHT_MODULE/NODE.
No existing browser profile, account, service, power setting or workflow is used.
"""
import json
import os
from pathlib import Path
import socket
import shutil
import subprocess
import sys
import time
import urllib.request

import pytest

ROOT = Path(__file__).parents[1]
NODE = os.environ.get('NODE') or shutil.which('node')
PLAYWRIGHT = os.environ.get('PLAYWRIGHT_MODULE', 'playwright')
pytestmark = pytest.mark.skipif(os.environ.get('SLEEP_IN_BROWSER_TEST') != '1', reason='Real isolated browser suite requires SLEEP_IN_BROWSER_TEST=1')


@pytest.fixture
def isolated_url(tmp_path):
    if not NODE:
        pytest.fail('Set NODE or provide Node.js on PATH for the opted-in browser suite')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        port = sock.getsockname()[1]
    env = {k: v for k, v in os.environ.items() if not k.startswith(('SLEEP_IN_', 'APP_', 'DATABASE_URL'))}
    env.update(APP_HOST='127.0.0.1', APP_PORT=str(port), APP_STATE_DIR=str(tmp_path),
               DATABASE_URL='sqlite:///' + str(tmp_path / 'browser.sqlite'), SLEEP_IN_LOCAL='1',
               SLEEP_IN_NODE=NODE, SLEEP_IN_PYTHON=sys.executable)
    process = subprocess.Popen([sys.executable, '-m', 'taskconsole', 'serve'], cwd=ROOT, env=env,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    url = f'http://127.0.0.1:{port}'
    try:
        for _ in range(100):
            if process.poll() is not None:
                pytest.fail('isolated application terminated during startup')
            try:
                with urllib.request.urlopen(url + '/healthz', timeout=.3) as response:
                    if response.status == 200:
                        break
            except OSError:
                time.sleep(.05)
        else:
            pytest.fail('isolated application failed to become healthy')
        yield url
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill(); process.wait(timeout=5)


def browser(isolated_url, body):
    source = r"""
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE);
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({locale:'zh-CN',viewport:{width:1440,height:900}});
const page=await context.newPage();page.setDefaultTimeout(30000);
const origin=process.env.TEST_ORIGIN;
const request=async(path,method='GET',body)=>page.evaluate(async({path,method,body})=>{
 const boot=await(await fetch('/api/bootstrap')).json();
 const r=await fetch(path,{method,headers:{'Content-Type':'application/json','X-CSRF-Token':boot.csrf},...(body!==undefined?{body:JSON.stringify(body)}:{})});
 const value=await r.json();if(!r.ok)throw new Error(JSON.stringify({status:r.status,value}));return value;
},{path,method,body});
const seed=async(graph)=>request('/api/workflows','POST',{name:'Browser synthetic',schedule:{kind:'manual'},timezone:'UTC',enabled:false,...graph});
const node=(id,extra={})=>({id,name:id,kind:'python',source:'def main(inputs): return inputs',inputs:{},config:{},...extra});
const open=async(w)=>{await page.goto(origin+'/workflows/'+w.id);await page.getByRole('button',{name:'Save draft',exact:true}).waitFor();};
const save=async(w)=>{const response=page.waitForResponse(r=>r.url().endsWith('/api/workflows/'+w.id)&&r.request().method()==='PUT');await page.getByRole('button',{name:'Save draft',exact:true}).click();assert.equal((await response).status(),200);return request('/api/workflows/'+w.id);};
try{
 await page.goto(origin);assert.equal(await page.locator('html').getAttribute('lang'),'en');
 await page.getByRole('button',{name:'Use default account',exact:true}).click();
 assert.equal(await page.locator('input[name="username"]').inputValue(),'admin');
 await page.getByRole('button',{name:'Sign in',exact:true}).click();
 await page.getByRole('button',{name:'New workflow',exact:true}).waitFor();
 """ + body + r"""
}finally{await context.close();await browser.close();}
"""
    result = subprocess.run([NODE, '--input-type=module', '-e', source], cwd=ROOT,
        env={**os.environ, 'PLAYWRIGHT_MODULE': PLAYWRIGHT, 'TEST_ORIGIN': isolated_url},
        text=True, capture_output=True, timeout=180)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def test_browser_sql_dialects_persist_only_compatible_connection(isolated_url):
    browser(isolated_url, r"""
 const dialects=['sqlite','postgresql','mysql','oracle'],ids={};
 for(const dialect of dialects){const c=await request('/api/connections','POST',{name:dialect,dialect,config:dialect==='sqlite'?{synthetic:true}:{host:'127.0.0.1',database:'synthetic',dbname:'synthetic',user:'synthetic',password:'synthetic-only',dsn:'127.0.0.1/synthetic'},write_enabled:false});ids[dialect]=c.id;}
 const w=await seed({nodes:[node('q',{kind:'sql',source:'SELECT 1',config:{dialect:'sqlite',mode:'query',connection_id:ids.sqlite}})],edges:[]});await open(w);
 await page.locator('article[data-node-id="q"]').click();
 for(const dialect of dialects){await page.getByLabel('Database dialect',{exact:true}).selectOption(dialect);const options=await page.getByLabel('Connection',{exact:true}).locator('option').evaluateAll(xs=>xs.map(x=>x.value));assert.deepEqual(options,['',ids[dialect]]);assert.equal(await page.getByLabel('Connection',{exact:true}).inputValue(),'');await page.getByLabel('Connection',{exact:true}).selectOption(ids[dialect]);assert.deepEqual(await page.getByLabel('SQL mode',{exact:true}).locator('option').evaluateAll(xs=>xs.map(x=>x.value)),['query','write']);await page.getByLabel('SQL mode',{exact:true}).selectOption('write');let saved=await save(w);assert.equal(saved.nodes[0].kind,'sql');assert.equal(saved.nodes[0].config.dialect,dialect);assert.equal(saved.nodes[0].config.connection_id,ids[dialect]);assert.equal(saved.nodes[0].config.mode,'write');await open(w);await page.locator('article[data-node-id="q"]').click();assert.equal(await page.getByLabel('Database dialect',{exact:true}).inputValue(),dialect);}
 await page.getByRole('button',{name:'Library',exact:true}).click();
 const labels=await page.locator('.wf-library-item [data-wf]').allTextContents();assert.deepEqual(labels.map(x=>x.trim()).sort(),['C','C++','Java','JavaScript','Python','SQL receiver','Shell'].sort());
 assert.deepEqual(await request('/api/workflow-runs'),[]);assert.equal((await request('/api/workflows/'+w.id)).published_version_id,null);
 """)


def test_browser_locale_preserves_unsaved_graph_source_and_mapping(isolated_url):
    browser(isolated_url, r"""
 const w=await seed({nodes:[node('a'),node('b')],edges:[]});await open(w);const writes=[];page.on('request',r=>{if(['POST','PUT'].includes(r.method()))writes.push(r.url());});
 await page.locator('article[data-node-id="b"]').click();
 await page.getByLabel('Name',{exact:true}).last().fill('中文 authored');
 const code='def main(inputs):\n    return {"中文": inputs}\n';await page.getByLabel('Source code',{exact:true}).fill(code);
 await page.getByRole('button',{name:'Inputs',exact:true}).click();await page.getByRole('button',{name:'Add input mapping',exact:true}).click();
 await page.getByLabel('Target field',{exact:true}).fill('draft_field');await page.getByLabel('Value (JSON or text)',{exact:true}).fill('{"nested":[false,0,"07:30"]}');
 await page.locator('[data-locale="zh-CN"]').click();assert.equal(await page.getByLabel('目标字段',{exact:true}).inputValue(),'draft_field');assert.equal(await page.getByLabel('值（JSON 或文本）',{exact:true}).inputValue(),'{"nested":[false,0,"07:30"]}');
 await page.locator('[data-locale="en"]').click();await page.getByRole('button',{name:'Map input',exact:true}).click();
 assert.ok((await page.locator('.wf-save-state').textContent()).includes('Unsaved'));
 await page.getByRole('button',{name:'Configuration',exact:true}).first().click();assert.equal(await page.getByLabel('Source code',{exact:true}).inputValue(),code);
 assert.equal(writes.length,0);let saved=await save(w);assert.equal(saved.nodes.find(n=>n.id==='b').source,code);assert.equal(saved.nodes.find(n=>n.id==='b').name,'中文 authored');assert.deepEqual(saved.nodes.find(n=>n.id==='b').inputs.draft_field,{source:'constant',value:{nested:[false,0,'07:30']}});
 await page.getByRole('button',{name:'Undo',exact:true}).click();saved=await save(w);assert.deepEqual(saved.nodes.find(n=>n.id==='b').inputs,{});
 await page.getByRole('button',{name:'Redo',exact:true}).click();saved=await save(w);assert.ok(saved.nodes.find(n=>n.id==='b').inputs.draft_field);await open(w);await page.locator('article[data-node-id="b"]').click();assert.equal(await page.getByLabel('Source code',{exact:true}).inputValue(),code);
 """)


def test_browser_lost_response_retry_survives_refresh(isolated_url):
    browser(isolated_url, r"""
 const w=await seed({nodes:[node('a')],edges:[]});await request('/api/workflows/'+w.id+'/publish','POST',{});await open(w);
 const bodies=[];let lost=true;
 await page.route('**/api/workflows/'+w.id+'/run',async route=>{bodies.push(route.request().postDataJSON());if(lost){lost=false;const r=await route.fetch();assert.equal(r.status(),202);await route.abort('failed');}else await route.continue();});
 await page.getByRole('button',{name:'Run published',exact:true}).click();await page.waitForFunction(()=>document.querySelector('.toast'));
 assert.equal((await request('/api/workflow-runs')).length,1);
 await page.reload();const response=page.waitForResponse(r=>r.url().endsWith('/api/workflows/'+w.id+'/run'));await page.getByRole('button',{name:'Run published',exact:true}).click();await response;
 assert.equal(bodies.length,2);assert.deepEqual(bodies[1],bodies[0]);assert.equal((await request('/api/workflow-runs')).length,1);
 """)


def test_browser_schedule_locale_weekdays_and_validation(isolated_url):
    browser(isolated_url, r"""
 const w=await seed({nodes:[node('a')],edges:[],params:{numeric:'001.50',time:'07:30'}});await open(w);await page.getByRole('button',{name:'Triggers',exact:true}).click();
 assert.equal(await page.getByLabel('Schedule enabled',{exact:true}).isDisabled(),true);
 await page.getByLabel('Schedule',{exact:true}).selectOption('weekly');await page.getByLabel('Thu',{exact:true}).check();await page.getByLabel('Time',{exact:true}).fill('07:30');await page.getByLabel('Timezone',{exact:true}).fill('Asia/Shanghai');
 const preview=async()=>{const r=page.waitForResponse(r=>r.url().endsWith('/preview'));await page.getByRole('button',{name:'Preview next five',exact:true}).click();return (await r).json();};
 const before=await preview();assert.equal(before.next_runs.length,5);assert.equal(before.timezone,'Asia/Shanghai');
 await page.getByLabel('Time',{exact:true}).focus();await page.locator('[data-locale="zh-CN"]').click();assert.equal(await page.getByLabel('时间',{exact:true}).inputValue(),'07:30');assert.equal(await page.getByLabel('时区',{exact:true}).inputValue(),'Asia/Shanghai');await page.locator('[data-locale="en"]').click();assert.deepEqual((await preview()).next_runs,before.next_runs);
 const response=page.waitForResponse(r=>r.url().endsWith('/api/workflows/'+w.id)&&r.request().method()==='PUT');await page.getByRole('button',{name:'Save draft',exact:true}).first().click();await response;let saved=await request('/api/workflows/'+w.id);assert.deepEqual(saved.schedule.weekdays,[0,3]);assert.equal(saved.schedule.time,'07:30');assert.equal(saved.timezone,'Asia/Shanghai');assert.equal(saved.enabled,false);assert.deepEqual(saved.params,{numeric:'001.50',time:'07:30'});
 await page.getByLabel('Schedule',{exact:true}).selectOption('weekdays');await page.getByLabel('Time',{exact:true}).fill('07:30');const weekdayPreview=await preview();await page.getByLabel('Time',{exact:true}).focus();await page.locator('[data-locale="zh-CN"]').click();await page.locator('[data-locale="en"]').click();assert.equal(await page.getByLabel('Time',{exact:true}).inputValue(),'07:30');assert.deepEqual((await preview()).next_runs,weekdayPreview.next_runs);const weekdaySave=page.waitForResponse(r=>r.url().endsWith('/api/workflows/'+w.id)&&r.request().method()==='PUT');await page.getByRole('button',{name:'Save draft',exact:true}).first().click();await weekdaySave;const weekdaySaved=await request('/api/workflows/'+w.id);assert.equal(weekdaySaved.schedule.kind,'weekdays');assert.equal(weekdaySaved.schedule.time,'07:30');assert.equal(weekdaySaved.timezone,'Asia/Shanghai');assert.equal(weekdaySaved.enabled,false);assert.deepEqual(weekdaySaved.params,{numeric:'001.50',time:'07:30'});
 await page.getByLabel('Starts on (optional)',{exact:true}).fill('2027-01-02T00:00:00Z');await page.getByLabel('Ends on (optional)',{exact:true}).fill('2027-01-01T00:00:00Z');const bad=await preview();assert.ok(bad.detail);assert.ok((await page.locator('.toast').textContent()).length>0);assert.equal(await page.getByLabel('Time',{exact:true}).inputValue(),'07:30');
 """)


def test_browser_duplicate_delete_cancel_undo_and_xss_literal(isolated_url):
    browser(isolated_url, r"""
 const literal='<img src=x onerror="window.syntheticInjected=true">';
 const w=await seed({nodes:[node('a',{name:literal}),node('b',{source:'def main(inputs): return inputs',inputs:{rows:{source:'node',node_id:'a',path:'rows'},nested:{source:'constant',value:{items:[false,0]}}}}),node('c')],edges:[{source:'a',target:'b'},{source:'b',target:'c'}]});await open(w);
 assert.ok((await page.locator('article[data-node-id="a"]').textContent()).includes(literal));assert.equal(await page.locator('article[data-node-id="a"] img').count(),0);assert.equal(await page.evaluate(()=>window.syntheticInjected),undefined);
 const before=await request('/api/workflows/'+w.id);const source=page.locator('.wf-outline-row').filter({has:page.getByRole('button',{name:'b',exact:true})});
 await page.locator('article[data-node-id="b"]').click();await page.locator('.wf-outline-row.selected').getByRole('button',{name:'Duplicate node',exact:true}).click();let saved=await save(w);const duplicate=saved.nodes.find(n=>!['a','b','c'].includes(n.id));assert.ok(duplicate);assert.deepEqual(duplicate.inputs,before.nodes.find(n=>n.id==='b').inputs);assert.deepEqual(saved.edges.filter(e=>e.target===duplicate.id),[{source:'a',target:duplicate.id}]);assert.equal(saved.edges.some(e=>e.source===duplicate.id),false);assert.notDeepEqual(duplicate.position,saved.nodes.find(n=>n.id==='b').position);
 await page.getByLabel('Name',{exact:true}).last().fill('Copy edited');await page.getByLabel('Source code',{exact:true}).fill('def main(inputs): return {"copy": True}');saved=await save(w);assert.equal(saved.nodes.find(n=>n.id==='b').name,'b');assert.equal(saved.nodes.find(n=>n.id==='b').source,before.nodes.find(n=>n.id==='b').source);
 await page.getByRole('button',{name:'Inputs',exact:true}).click();await page.locator('.wf-binding').filter({has:page.locator('strong').filter({hasText:/^nested$/})}).getByRole('button',{name:'Configuration',exact:true}).click();await page.getByLabel('Value (JSON or text)',{exact:true}).fill('{"items":[false,0,99]}');await page.getByRole('button',{name:'Map input',exact:true}).click();saved=await save(w);assert.deepEqual(saved.nodes.find(n=>n.id==='b').inputs.nested.value,{items:[false,0]});assert.deepEqual(saved.nodes.find(n=>n.id===duplicate.id).inputs.nested.value,{items:[false,0,99]});
 await page.locator('.wf-outline-row.selected').getByRole('button',{name:'Delete node',exact:true}).click();await page.getByRole('button',{name:'Cancel',exact:true}).click();saved=await save(w);assert.equal(saved.nodes.length,4);
 await page.locator('.wf-outline-row.selected').getByRole('button',{name:'Delete node',exact:true}).click();await page.getByRole('button',{name:'Delete from draft',exact:true}).click();saved=await save(w);assert.equal(saved.nodes.length,3);
 await page.getByRole('button',{name:'Undo',exact:true}).click();saved=await save(w);assert.equal(saved.nodes.length,4);assert.equal(saved.nodes.find(n=>n.id===duplicate.id).name,'Copy edited');await page.getByRole('button',{name:'Redo',exact:true}).click();saved=await save(w);assert.equal(saved.nodes.length,3);
 """)


def test_browser_structure_insert_branch_rewire_rejects_cycle(isolated_url):
    browser(isolated_url, r"""
 const w=await seed({nodes:[node('a'),node('b',{inputs:{rows:{source:'node',node_id:'a',path:'rows'}}}),node('c')],edges:[{source:'a',target:'b',condition:{path:'ok',operator:'truthy'}}]});await open(w);
 await page.locator('.wf-insert-edge').click();await page.getByRole('button',{name:'Create node',exact:true}).click();let saved=await save(w);const inserted=saved.nodes.find(n=>!['a','b','c'].includes(n.id));assert.ok(inserted);assert.deepEqual(inserted.inputs,{});assert.equal(saved.nodes.find(n=>n.id==='b').inputs.rows.node_id,'a');assert.deepEqual(saved.edges.find(e=>e.source==='a').condition,{path:'ok',operator:'truthy'});
 await page.locator('article[data-node-id="a"]').getByRole('button',{name:'Add next step',exact:true}).click();await page.getByRole('button',{name:'Create node',exact:true}).click();saved=await save(w);assert.equal(saved.nodes.length,5);assert.equal(saved.edges.filter(e=>e.source==='a').length,2);
 await page.locator('.wf-edge-hit').first().focus();await page.keyboard.press('Enter');await page.getByLabel('From node',{exact:true}).selectOption('b');await page.getByLabel('To node',{exact:true}).selectOption(inserted.id);const before=await request('/api/workflows/'+w.id);await page.getByRole('button',{name:'Apply connection',exact:true}).click();await page.getByText('This connection would create a cycle.',{exact:true}).waitFor();saved=await save(w);assert.deepEqual(saved.nodes,before.nodes);assert.deepEqual(saved.edges,before.edges);
 await page.getByLabel('From node',{exact:true}).selectOption('a');await page.getByLabel('To node',{exact:true}).selectOption('c');await page.getByRole('button',{name:'Apply connection',exact:true}).click();saved=await save(w);assert.ok(saved.edges.some(e=>e.source==='a'&&e.target==='c'));assert.equal(saved.nodes.find(n=>n.id==='b').inputs.rows.node_id,'a');await page.getByRole('button',{name:'Undo',exact:true}).click();saved=await save(w);assert.deepEqual(saved.edges,before.edges);
 """)


def test_browser_password_change_hides_default_card_in_two_sessions(isolated_url):
    browser(isolated_url, r"""
 const boot=await request('/api/bootstrap'),old=boot.local_account;
 const other=await browser.newContext();const second=await other.newPage();
 try{
  await second.goto(origin);await second.getByRole('button',{name:'Use default account',exact:true}).click();await second.getByRole('button',{name:'Sign in',exact:true}).click();await second.getByRole('button',{name:'New workflow',exact:true}).waitFor();
  await page.goto(origin+'/account');await page.getByLabel('Current password',{exact:true}).fill(old.password);await page.getByLabel('New password',{exact:true}).fill('synthetic-private-replacement');await page.locator('form button[type=submit]').click();await page.getByRole('button',{name:'Sign in',exact:true}).waitFor();
  assert.equal(await page.getByRole('button',{name:'Use default account',exact:true}).count(),0);assert.equal(await page.locator('body').textContent().then(t=>t.includes('synthetic-private-replacement')),false);
  await second.reload();await second.getByRole('button',{name:'Sign in',exact:true}).waitFor();assert.equal(await second.getByRole('button',{name:'Use default account',exact:true}).count(),0);
  const noHint=await page.evaluate(async()=> (await(await fetch('/api/bootstrap')).json()).local_account);assert.equal(noHint,null);
  await page.locator('input[name="username"]').fill(old.username);await page.locator('input[name="password"]').fill(old.password);const denied=page.waitForResponse(r=>r.url().endsWith('/api/login'));await page.getByRole('button',{name:'Sign in',exact:true}).click();assert.equal((await denied).status(),401);
  await page.locator('input[name="username"]').fill(old.username);await page.locator('input[name="password"]').fill('synthetic-private-replacement');const accepted=page.waitForResponse(r=>r.url().endsWith('/api/login'));await page.getByRole('button',{name:'Sign in',exact:true}).click();const loginResponse=await accepted;assert.equal(loginResponse.status(),200,await loginResponse.text());await page.getByRole('button',{name:'New workflow',exact:true}).waitFor();
 }finally{await other.close();}
 """)


def test_browser_notification_retry_is_explicit_and_targets_failed_delivery(isolated_url, tmp_path):
    from taskconsole.store import Store, stamp
    from taskconsole.workflows_operations import WorkflowOperations
    store = Store(tmp_path, 'sqlite:///' + str(tmp_path / 'browser.sqlite'))
    op = WorkflowOperations(store)
    op.save_channel({'name': 'Synthetic no-send channel', 'kind': 'webhook', 'config': {'url': 'http://127.0.0.1:1/synthetic'}}, 'synthetic-channel')
    with store.transaction() as tx:
        for key, status in [('failed-delivery', 'failed'), ('uncertain-delivery', 'uncertain')]:
            tx.put('workflow_notification', {'id': key, 'run_id': 'synthetic-run', 'workflow_id': 'synthetic-workflow',
                'channel_id': 'synthetic-channel', 'event': 'failed', 'status': status, 'attempts': 1,
                'created_at': stamp(), 'error': 'Synthetic fixture, never sent'})
    browser(isolated_url, r"""
 const posts=[];page.on('request',r=>{if(r.method()==='POST')posts.push(r.url());});await page.goto(origin+'/workflows/operations');
 await page.getByText('Delivery history',{exact:true}).click();
 assert.equal(await page.getByRole('button',{name:'Retry failed delivery',exact:true}).count(),1);assert.equal(posts.length,0);
 assert.ok((await page.locator('body').textContent()).includes('Synthetic no-send channel'));
 const response=page.waitForResponse(r=>r.url().endsWith('/api/workflow-notifications/failed-delivery/retry'));await page.getByRole('button',{name:'Retry failed delivery',exact:true}).click();const r=await response;assert.equal(r.status(),200);assert.deepEqual(r.request().postDataJSON(),{expected_attempt:1});assert.equal((await r.json()).status,'pending');
 assert.equal(posts.filter(x=>x.endsWith('/retry')).length,1);assert.equal(posts.some(x=>x.endsWith('/test')||x.endsWith('/run')),false);
 assert.equal((await request('/api/workflow-notifications')).find(d=>d.id==='uncertain-delivery').status,'uncertain');
 """)


@pytest.fixture
def engine_url(isolated_url, tmp_path):
    command = os.environ.get('SLEEP_IN_TEST_N8N_COMMAND') or os.environ.get('SLEEP_IN_N8N_COMMAND')
    if not command:
        pytest.skip('Actual isolated n8n browser fixture requires SLEEP_IN_TEST_N8N_COMMAND')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('SLEEP_IN_', 'APP_', 'DATABASE_URL'))}
    env.update(APP_HOST='127.0.0.1', APP_STATE_DIR=str(tmp_path), DATABASE_URL='sqlite:///' + str(tmp_path / 'browser.sqlite'),
        SLEEP_IN_NODE=NODE, SLEEP_IN_PYTHON=sys.executable, SLEEP_IN_N8N_COMMAND=command, SLEEP_IN_BASE_URL=isolated_url)
    source = 'import os; from taskconsole.store import Store; from taskconsole.workflows import WorkflowService; from taskconsole.workflows_n8n import worker_loop; worker_loop(WorkflowService(Store(os.environ["APP_STATE_DIR"],os.environ["DATABASE_URL"])))'
    process = subprocess.Popen([sys.executable, '-c', source], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        yield isolated_url
    finally:
        process.terminate()
        try:
            process.wait(timeout=25)
        except subprocess.TimeoutExpired:
            import signal
            os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)


def test_browser_field_picker_executes_exact_sql_python_js_with_real_n8n(engine_url, tmp_path):
    browser(engine_url, r"""
 const templates=await request('/api/workflow-templates'),{id,...template}=templates[0];template.nodes.find(n=>n.id==='summary').inputs={};template.nodes.find(n=>n.id==='report').inputs={};template.edges=[];
 const w=await seed(template);await open(w);
 for(const [target,key,source,path] of [['summary','orders','orders','rows'],['report','summary','summary','summary']]){
   await page.locator('article[data-node-id="'+target+'"]').click();await page.getByRole('button',{name:'Inputs',exact:true}).click();await page.getByRole('button',{name:'Add input mapping',exact:true}).click();await page.getByLabel('Target field',{exact:true}).fill(key);await page.getByLabel('Source',{exact:true}).selectOption('node');await page.getByLabel('Upstream output',{exact:true}).selectOption(source);await page.getByLabel('Output field',{exact:true}).selectOption(JSON.stringify([path]));await page.getByRole('button',{name:'Map input',exact:true}).click();
 }
 const saved=await save(w);assert.deepEqual(saved.edges.map(e=>[e.source,e.target]),[['orders','summary'],['summary','report']]);assert.equal(saved.nodes.find(n=>n.id==='summary').inputs.orders.node_id,'orders');
 const admitted=page.waitForResponse(r=>r.url().endsWith('/api/workflows/'+w.id+'/run'));await page.getByRole('button',{name:'Test workflow',exact:true}).click();const run=await(await admitted).json();assert.equal(run.status,'queued');
 let result;const deadline=Date.now()+90000;do{result=await request('/api/workflow-runs/'+run.id);if(!['queued','running','cancelling'].includes(result.status))break;await page.waitForTimeout(250);}while(Date.now()<deadline);assert.equal(result.status,'succeeded',result.error);assert.equal(result.engine,'n8n');assert.deepEqual(result.nodes.summary.output.data.summary,{count:3,total:'30.75'});assert.deepEqual(result.nodes.report.inputs,{summary:{count:3,total:'30.75'}});assert.deepEqual(result.nodes.summary.inputs.orders,[{order_id:'A001',amount:'10.50',region:'华东'},{order_id:'A002',amount:'20.25',region:null},{order_id:'A003',amount:'0.00',region:'西部'}]);assert.ok(Object.values(result.nodes).every(n=>n.attempts.length===1));
 await page.goto(origin+'/workflow-runs/'+run.id);await page.locator('.wf-run-summary').getByText('Succeeded',{exact:true}).waitFor();const download=page.waitForEvent('download');await page.getByRole('link',{name:'report.txt',exact:true}).click();const file=await download;const stream=await file.createReadStream();let text='';for await(const chunk of stream)text+=chunk.toString();assert.equal(text,'3 orders • 30.75\n');
 """)

    from taskconsole.store import Store
    with Store(tmp_path, 'sqlite:///' + str(tmp_path / 'browser.sqlite')).transaction() as tx:
        records = tx.all('workflow_run')
    assert len(records) == 1 and records[0].get('n8n_execution_id')


def test_browser_node_test_explicit_input_and_stale_sample_with_real_n8n(engine_url):
    browser(engine_url, r"""
 const w=await seed({nodes:[node('upstream',{source:'def main(inputs): raise RuntimeError("must not run")'}),node('consumer')],edges:[{source:'upstream',target:'consumer'}]});await open(w);await page.locator('article[data-node-id="consumer"]').click();await page.locator('.wf-node-test > summary').click();await page.getByLabel('Sample input object (JSON)',{exact:true}).fill('{"message":"hello","flag":false}');
 const admitted=page.waitForResponse(r=>r.url().endsWith('/nodes/consumer/test'));await page.getByRole('button',{name:'Test this node',exact:true}).click();const response=await admitted;assert.deepEqual(response.request().postDataJSON(),{inputs:{message:'hello',flag:false}});const run=await response.json();
 let result;const deadline=Date.now()+90000;do{result=await request('/api/workflow-runs/'+run.id);if(!['queued','running','cancelling'].includes(result.status))break;await page.waitForTimeout(250);}while(Date.now()<deadline);assert.equal(result.status,'succeeded',result.error);assert.deepEqual(Object.keys(result.nodes),['consumer']);assert.deepEqual(result.test_input_source,{kind:'explicit'});assert.deepEqual(result.nodes.consumer.output.data,{message:'hello',flag:false});assert.equal(result.nodes.consumer.attempts.length,1);
 await page.getByLabel('Source code',{exact:true}).fill('def main(inputs): return {"edited": True}');await page.getByRole('button',{name:'Inputs',exact:true}).click();await page.getByText('Stale sample — draft changed',{exact:true}).waitFor();assert.ok((await page.locator('.wf-inspector').textContent()).includes(run.id));
 const saved=await save(w);assert.deepEqual(saved.nodes.find(n=>n.id==='consumer').inputs,{});const sample=await request('/api/workflow-runs/'+run.id+'/nodes/consumer/sample');assert.equal(sample.stale,true);assert.deepEqual(sample.inputs,{message:'hello',flag:false});
 """)


def test_browser_ports_deduplicate_blank_drop_and_preserve_unmapped_data(isolated_url):
    browser(isolated_url, r"""
 const w=await seed({nodes:[node('a'),node('b'),node('c')],edges:[]});await open(w);
 const gesture=async(from,to)=>{const a=await page.locator('article[data-node-id="'+from+'"] .output-port').boundingBox(),b=to?await page.locator('article[data-node-id="'+to+'"] .input-port').boundingBox():await page.locator('.wf-canvas').boundingBox();await page.mouse.move(a.x+a.width/2,a.y+a.height/2);await page.mouse.down();await page.mouse.move(to?b.x+b.width/2:b.x+20,to?b.y+b.height/2:b.y+20,{steps:8});await page.mouse.up();};
 await gesture('a','b');let saved=await save(w);assert.deepEqual(saved.edges,[{source:'a',target:'b'}]);assert.ok(saved.nodes.every(n=>Object.keys(n.inputs).length===0));const positions=saved.nodes.map(n=>n.position);
 await gesture('a','b');saved=await save(w);assert.equal(saved.edges.length,1);assert.deepEqual(saved.nodes.map(n=>n.position),positions);
 await gesture('c',null);saved=await save(w);assert.equal(saved.edges.length,1);assert.deepEqual(saved.nodes.map(n=>n.position),positions);assert.ok(saved.nodes.every(n=>Object.keys(n.inputs).length===0));
 assert.ok((await page.locator('.wf-canvas-note').textContent()).includes('Connections set execution order. Input mappings decide data.'));assert.deepEqual(await request('/api/workflow-runs'),[]);assert.equal(saved.published_version_id,null);
 """)


def test_browser_self_cycle_rejection_keeps_graph_history_and_api_gate(isolated_url):
    browser(isolated_url, r"""
 const w=await seed({nodes:[node('a'),node('b'),node('c')],edges:[{source:'a',target:'b'},{source:'b',target:'c'}]});await open(w);await page.locator('article[data-node-id="b"]').click();await page.getByLabel('Name',{exact:true}).last().fill('prior valid edit');let expected=await save(w);
 const attempt=async(from,to)=>{const a=await page.locator('article[data-node-id="'+from+'"] .output-port').boundingBox(),b=await page.locator('article[data-node-id="'+to+'"] .input-port').boundingBox();await page.mouse.move(a.x+a.width/2,a.y+a.height/2);await page.mouse.down();await page.mouse.move(b.x+b.width/2,b.y+b.height/2,{steps:8});await page.mouse.up();await page.getByText('This connection would create a cycle. '+from+' → '+to,{exact:true}).waitFor();const saved=await save(w);assert.deepEqual(saved.nodes,expected.nodes);assert.deepEqual(saved.edges,expected.edges);};
 await attempt('a','a');await attempt('c','a');
 await page.locator('article[data-node-id="a"]').click();await page.getByRole('button',{name:'Inputs',exact:true}).click();await page.getByRole('button',{name:'Add input mapping',exact:true}).click();await page.getByLabel('Source',{exact:true}).selectOption('node');assert.deepEqual(await page.getByLabel('Upstream output',{exact:true}).locator('option').evaluateAll(xs=>xs.map(x=>x.value)),['']);await page.getByRole('button',{name:'Close',exact:true}).last().click();
 await page.getByRole('button',{name:'Undo',exact:true}).click();let undone=await save(w);assert.equal(undone.nodes.find(n=>n.id==='b').name,'b');await page.getByRole('button',{name:'Redo',exact:true}).click();let redone=await save(w);assert.deepEqual(redone.nodes,expected.nodes);assert.deepEqual(redone.edges,expected.edges);
 for(const edge of [{source:'a',target:'a'},{source:'c',target:'a'}]){const invalid=await seed({nodes:[node('a'),node('b'),node('c')],edges:[...expected.edges,edge]});const response=await page.evaluate(async id=>{const boot=await(await fetch('/api/bootstrap')).json();const r=await fetch('/api/workflows/'+id+'/publish',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':boot.csrf},body:'{}'});return {status:r.status,value:await r.json()};},invalid.id);assert.ok([400,422].includes(response.status));assert.ok(JSON.stringify(response.value).toLowerCase().includes('cycle'));assert.equal((await request('/api/workflows/'+invalid.id)).published_version_id,null);}
 """)


def test_browser_complete_history_and_disabled_controls_preserve_publication(isolated_url, tmp_path):
    output=browser(isolated_url, r"""
 const w=await seed({nodes:[node('a'),node('b'),node('c')],edges:[]});const publication=await request('/api/workflows/'+w.id+'/publish','POST',{});await open(w);
 assert.equal(await page.getByRole('button',{name:'Undo',exact:true}).isDisabled(),true);assert.equal(await page.getByRole('button',{name:'Redo',exact:true}).isDisabled(),true);
 const logical=v=>({nodes:v.nodes.map(({position,...n})=>n),edges:v.edges});const states=[logical(await request('/api/workflows/'+w.id))];const record=async()=>states.push(logical(await save(w)));
 await page.getByRole('button',{name:'Library',exact:true}).click();await page.locator('.wf-library-item').filter({has:page.locator('[data-wf="python"]')}).click();await record();const added=states.at(-1).nodes.find(n=>!['a','b','c'].includes(n.id)).id;await page.getByRole('button',{name:'Fit canvas',exact:true}).click();
 await page.locator('article[data-node-id="a"] .output-port').click();await page.locator('article[data-node-id="'+added+'"] .input-port').click();await record();await page.locator('article[data-node-id="'+added+'"]').click();await page.getByRole('button',{name:'Inputs',exact:true}).click();await page.getByRole('button',{name:'Add input mapping',exact:true}).click();await page.getByLabel('Target field',{exact:true}).fill('constant');await page.getByLabel('Value (JSON or text)',{exact:true}).fill('{"deep":[false,0]}');await page.getByRole('button',{name:'Map input',exact:true}).click();await record();
 await page.getByRole('button',{name:'Configuration',exact:true}).first().click();await page.getByLabel('Name',{exact:true}).last().fill('renamed');await record();await page.locator('article[data-node-id="'+added+'"]').press('Delete');await page.getByRole('button',{name:'Delete from draft',exact:true}).click();await record();
 for(let i=states.length-2;i>=0;i--){await page.getByRole('button',{name:'Undo',exact:true}).click();assert.deepEqual(logical(await save(w)),states[i]);}assert.equal(await page.getByRole('button',{name:'Undo',exact:true}).isDisabled(),true);
 for(let i=1;i<states.length;i++){await page.getByRole('button',{name:'Redo',exact:true}).click();assert.deepEqual(logical(await save(w)),states[i]);}assert.equal(await page.getByRole('button',{name:'Redo',exact:true}).isDisabled(),true);
 await page.getByRole('button',{name:'Undo',exact:true}).click();await page.locator('article[data-node-id="'+added+'"]').click();await page.getByLabel('Name',{exact:true}).last().fill('different');assert.equal(await page.getByRole('button',{name:'Redo',exact:true}).isDisabled(),true);const saved=await save(w);assert.equal(saved.published_version_id,publication.version_id);console.log(JSON.stringify({id:publication.version_id}));
 """)
    from taskconsole.store import Store
    publication_id = json.loads(output)['id']
    with Store(tmp_path, 'sqlite:///' + str(tmp_path / 'browser.sqlite')).transaction() as tx:
        publication = tx.get('workflow_version', publication_id)
    assert [n['id'] for n in publication['snapshot']['nodes']] == ['a', 'b', 'c']
    assert publication['snapshot']['edges'] == []


def test_browser_invalid_lost_admission_recovers_after_atomic_retirement(isolated_url):
    browser(isolated_url, r"""
 const w=await seed({nodes:[node('a')],edges:[],params:{count:'wrong'},parameter_schema:{type:'object',required:['count'],properties:{count:{type:'integer'}}}});await request('/api/workflows/'+w.id+'/publish','POST',{});await open(w);
 const bodies=[];let lost=true;await page.route('**/api/workflows/'+w.id+'/run',async route=>{bodies.push(route.request().postDataJSON());if(lost){lost=false;await route.abort('failed');}else await route.continue();});
 await page.getByRole('button',{name:'Run published',exact:true}).click();await page.locator('.toast').waitFor();assert.deepEqual(await request('/api/workflow-runs'),[]);
 await page.reload();const resolution=page.waitForResponse(r=>r.url().endsWith('/admission-resolution'));await page.getByRole('button',{name:'Run published',exact:true}).click();const resolved=await resolution;assert.equal(resolved.status(),200);assert.deepEqual(await resolved.json(),{status:'retired',idempotency_key:bodies[0].idempotency_key});assert.deepEqual(bodies[1],bodies[0]);assert.deepEqual(await request('/api/workflow-runs'),[]);
 await page.getByRole('button',{name:'Settings',exact:true}).click();await page.getByLabel('Workflow parameters (JSON)',{exact:true}).fill('{"count":7}');await page.getByLabel('Description',{exact:true}).click();const response=page.waitForResponse(r=>r.url().endsWith('/api/workflows/'+w.id)&&r.request().method()==='PUT');await page.getByRole('button',{name:'Save draft',exact:true}).last().click();assert.equal((await response).status(),200);
 await page.reload();const admitted=page.waitForResponse(r=>r.url().endsWith('/api/workflows/'+w.id+'/run'));await page.getByRole('button',{name:'Run published',exact:true}).click();const admission=await admitted;assert.equal(admission.status(),202);const run=await admission.json();assert.deepEqual(run.params,{count:7});assert.notEqual(bodies[2].idempotency_key,bodies[0].idempotency_key);assert.equal((await request('/api/workflow-runs')).length,1);
 const late=await page.evaluate(async({id,body})=>{const boot=await(await fetch('/api/bootstrap')).json();const r=await fetch('/api/workflows/'+id+'/run',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':boot.csrf},body:JSON.stringify({...body,params:{count:9}})});return {status:r.status,value:await r.json()};},{id:w.id,body:bodies[0]});assert.equal(late.status,422);assert.equal(late.value.detail.code,'admission_retired');assert.equal((await request('/api/workflow-runs')).length,1);
 """)


def test_browser_edge_only_delete_validation_link_undo_and_alternate_path(isolated_url):
    browser(isolated_url, r"""
 const {id,...template}=(await request('/api/workflow-templates'))[0];const w=await seed(template);await open(w);const summary=w.nodes.find(n=>n.id==='summary'),orders=w.nodes.find(n=>n.id==='orders'),original=summary.inputs.orders;
 const remove=async()=>{await page.getByRole('button',{name:orders.name+' → '+summary.name,exact:true}).press('Enter');await page.getByRole('button',{name:'Remove connection',exact:true}).click();};
 await remove();assert.ok((await page.locator('.wf-structure-warning').textContent()).includes(summary.name));let saved=await save(w);assert.deepEqual(saved.nodes.find(n=>n.id==='summary').inputs.orders,original);assert.ok(saved.validation_errors.some(e=>e.node_id==='summary'));assert.equal(saved.edges.some(e=>e.source==='orders'&&e.target==='summary'),false);
 const rejection=page.waitForResponse(r=>r.url().endsWith('/publish'));await page.getByRole('button',{name:'Publish',exact:true}).click();assert.equal((await rejection).status(),422);assert.equal((await request('/api/workflows/'+w.id)).published_version_id,null);await page.locator('.wf-validation button').filter({hasText:summary.name}).first().click();assert.equal(await page.locator('article[data-node-id="summary"]').getAttribute('class').then(s=>s.includes('selected')),true);
 await page.getByRole('button',{name:'Undo',exact:true}).click();saved=await save(w);assert.deepEqual(saved.nodes.find(n=>n.id==='summary').inputs.orders,original);assert.ok(saved.edges.some(e=>e.source==='orders'&&e.target==='summary'));assert.equal(saved.validation_errors.length,0);assert.equal(await page.locator('.wf-structure-warning').count(),0);
 const alternate=await seed({...template,nodes:[...template.nodes,node('bridge')],edges:[...template.edges,{source:'orders',target:'bridge'},{source:'bridge',target:'summary'}]});await open(alternate);await remove();const valid=await save(alternate);assert.deepEqual(valid.nodes.find(n=>n.id==='summary').inputs.orders,original);assert.equal(valid.validation_errors.length,0);const published=await request('/api/workflows/'+alternate.id+'/publish','POST',{});assert.ok(published.version_id);assert.deepEqual(await request('/api/workflow-runs'),[]);
 """)
