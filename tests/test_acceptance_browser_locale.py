"""Isolated EN/ZH surface evidence; screenshots require separate human review."""
import json

import pytest

from test_acceptance_browser import browser, engine_url, isolated_url, pytestmark
from test_acceptance_browser_accessibility import evidence_directory
from test_acceptance_browser_mapping import HELPERS, durable_runs


EVIDENCE = r"""
const {writeFile}=await import('node:fs/promises');
const records=[];
const capture=async(name)=>{
 const locale=await page.locator('html').getAttribute('lang');
 records.push({name,locale,...await page.evaluate(()=>({text:document.querySelector('#app').innerText,controls:[...document.querySelectorAll('button,input,select,textarea,a')].filter(n=>n.getBoundingClientRect().width).map(n=>({tag:n.tagName,text:n.textContent,aria:n.getAttribute('aria-label'),title:n.title,labels:[...(n.labels||[])].map(l=>l.textContent),type:n.type})),labelTargets:[...document.querySelectorAll('label[for]')].map(l=>({text:l.textContent,exists:!!document.getElementById(l.htmlFor)}))}))});
 assert.ok(records.at(-1).labelTargets.every(x=>x.exists));
 await page.screenshot({path:imageDir+'/'+name+'-'+locale+'.png',fullPage:true});
 await writeFile(imageDir+'/locale-'+bundle+'-review.json',JSON.stringify(records,null,2));
};
const locale=async value=>{await page.locator('[data-locale="'+value+'"]').click();assert.equal(await page.locator('html').getAttribute('lang'),value);};
"""


def test_browser_chinese_os_starts_english_and_login_switch_preserves_credentials(isolated_url,tmp_path):
    browser(isolated_url, 'const imageDir='+json.dumps(str(evidence_directory(tmp_path)))+';'+r"""
 const fresh=await browser.newContext({locale:'zh-CN',viewport:{width:1440,height:900}});
 try{const page=await fresh.newPage();page.setDefaultTimeout(30000);const bundle='login';
 """+EVIDENCE+r"""
 await page.goto(origin);await page.getByRole('button',{name:'Sign in',exact:true}).waitFor();assert.equal(await page.evaluate(()=>navigator.language),'zh-CN');assert.equal(await page.locator('html').getAttribute('lang'),'en');assert.equal(await page.evaluate(()=>localStorage.getItem('taskconsole.locale')),null);
 await page.getByRole('button',{name:'Use default account',exact:true}).click();const credentials=await page.locator('form input').evaluateAll(xs=>xs.map(x=>({name:x.name,value:x.value})));assert.equal(credentials.find(x=>x.name==='username').value,'admin');await capture('login');
 await locale('zh-CN');await page.getByRole('button',{name:'使用默认账户',exact:true}).waitFor();assert.deepEqual(await page.locator('form input').evaluateAll(xs=>xs.map(x=>({name:x.name,value:x.value}))),credentials);await capture('login');
 await page.getByLabel('密码',{exact:true}).fill('invalid-synthetic-password');let response=page.waitForResponse(r=>r.url().endsWith('/api/login'));await page.getByRole('button',{name:'登录',exact:true}).click();assert.equal((await response).status(),401);await page.getByText('请检查用户名和密码。',{exact:true}).waitFor();await capture('login-error');
 await locale('en');await page.getByRole('button',{name:'Use default account',exact:true}).click();await page.getByLabel('Password',{exact:true}).fill('invalid-synthetic-password');response=page.waitForResponse(r=>r.url().endsWith('/api/login'));await page.getByRole('button',{name:'Sign in',exact:true}).click();assert.equal((await response).status(),401);assert.ok((await page.locator('#toast').textContent()).includes('Check your username and password.'));await capture('login-error');
 await locale('zh-CN');await page.getByRole('button',{name:'使用默认账户',exact:true}).click();await page.getByRole('button',{name:'登录',exact:true}).click();await page.getByRole('button',{name:'新建工作流',exact:true}).waitFor();await capture('empty-workflows');await locale('en');await page.getByRole('button',{name:'New workflow',exact:true}).waitFor();await capture('empty-workflows');
 }finally{await fresh.close();}
 """)


def test_browser_locale_all_named_surfaces_keep_authored_graph_params_and_run_data(engine_url,tmp_path):
    browser(engine_url,HELPERS+'const imageDir='+json.dumps(str(evidence_directory(tmp_path)))+';const bundle="surfaces";'+EVIDENCE+r"""
 const code='def main(inputs):\n    print("Authored 日志 must stay literal")\n    return inputs\n';const data={中文:'literal English 数据',zero:0,no:false,empty:null};const w=await seed({name:'Authored 工作流 English',description:'Authored description 中文',params:{numeric:'001.50',time:'07:30',payload:data},nodes:[node('a',{position:{x:80,y:100},name:'Authored 节点 English',source:code,inputs:{payload:{source:'parameter',path:['payload']}}}),node('b',{position:{x:80,y:300},name:'Consumer 中文',inputs:{copy:{source:'node',node_id:'a',path:['payload']}}})],edges:[{source:'a',target:'b'}]});const expected=await request('/api/workflows/'+w.id);
 const pair=async(name,check)=>{await locale('en');await check('en');await capture(name);await locale('zh-CN');await check('zh-CN');await capture(name);};
 await page.goto(origin+'/workflow-runs');await page.getByText('No runs yet. Test your workflow to inspect the results.',{exact:true}).waitFor();await pair('empty-runs',async lang=>{await page.getByText(lang==='en'?'No runs yet. Test your workflow to inspect the results.':'暂无运行。测试工作流以查看结果。',{exact:true}).waitFor();});
 await page.goto(origin+'/templates');await page.locator('.wf-template').first().waitFor();const catalog=(await request('/api/workflow-templates')).flatMap(v=>[v.name,v.description]);await pair('catalog',async lang=>{await page.getByRole('button',{name:lang==='en'?'Guided setup':'引导设置',exact:true}).first().waitFor();assert.deepEqual(await page.locator('.wf-template h2,.wf-template p').allTextContents(),lang==='en'?catalog:['晨间报告','示例 SQLite 订单 → Python 汇总 → JavaScript 报告，无需外部账户。']);});
 await locale('en');await open(w);await page.getByRole('button',{name:'Library',exact:true}).click();await pair('editor-library',async lang=>{await page.getByRole('button',{name:lang==='en'?'Save draft':'保存草稿',exact:true}).waitFor();assert.equal(await page.getByRole('button',{name:lang==='en'?'Fit canvas':'适应画布',exact:true}).getAttribute('title'),lang==='en'?'Fit canvas':'适应画布');assert.equal(await page.locator('article[data-node-id="a"] .wf-node-name').textContent(),'Authored 节点 English');assert.equal(await page.locator('article[data-node-id="a"] .output-port').getAttribute('aria-label'),lang==='en'?'Output port · Bottom':'输出端口 · 下');assert.equal(await page.locator('article[data-node-id="a"] .input-port').getAttribute('aria-label'),lang==='en'?'Input port · Top':'输入端口 · 上');assert.equal(await page.locator('article[data-node-id="a"] .wf-add-next').getAttribute('title'),lang==='en'?'Add next step':'添加下一步');assert.equal(await page.locator('article[data-node-id="a"] .wf-add-next').getAttribute('aria-label'),lang==='en'?'Add next step':'添加下一步');});
 await page.locator('article[data-node-id="a"]').click();await pair('inspector-code',async lang=>{assert.equal(await page.getByLabel(lang==='en'?'Source code':'源代码',{exact:true}).inputValue(),code);assert.equal(await page.getByLabel(lang==='en'?'Name':'名称',{exact:true}).last().inputValue(),'Authored 节点 English');});
 await locale('en');await page.getByRole('button',{name:'Inputs',exact:true}).click();await pair('inspector-inputs',async lang=>{await page.getByRole('button',{name:lang==='en'?'Add input mapping':'添加输入映射',exact:true}).waitFor();assert.ok((await page.locator('.wf-binding').textContent()).includes('payload'));});
 await locale('en');await page.getByRole('button',{name:'Triggers',exact:true}).click();await page.getByLabel('Schedule',{exact:true}).selectOption('weekly');await page.getByLabel('Time',{exact:true}).fill('07:30');await page.getByLabel('Timezone',{exact:true}).fill('Asia/Shanghai');await pair('schedule',async lang=>{assert.equal(await page.getByLabel(lang==='en'?'Time':'时间',{exact:true}).inputValue(),'07:30');assert.equal(await page.getByLabel(lang==='en'?'Timezone':'时区',{exact:true}).inputValue(),'Asia/Shanghai');await page.getByRole('button',{name:lang==='en'?'Preview next five':'预览接下来五次',exact:true}).waitFor();});
 await locale('en');await page.getByRole('button',{name:'Settings',exact:true}).click();await pair('parameters',async lang=>{assert.deepEqual(JSON.parse(await page.getByLabel(lang==='en'?'Workflow parameters (JSON)':'工作流参数（JSON）',{exact:true}).inputValue()),expected.params);assert.equal(await page.getByLabel(lang==='en'?'Description':'描述',{exact:true}).inputValue(),expected.description);});
 await locale('en');await page.getByRole('button',{name:'Editor',exact:true}).click();const saved=await save(w);assert.deepEqual(saved.nodes,expected.nodes);assert.deepEqual(saved.edges,expected.edges);assert.deepEqual(saved.params,expected.params);assert.equal(saved.name,expected.name);assert.equal(saved.schedule.time,'07:30');assert.equal(saved.timezone,'Asia/Shanghai');
 await page.getByRole('button',{name:'Editor',exact:true}).click();const run=await testRun(w);assert.equal(run.status,'succeeded');assert.deepEqual(run.nodes.a.output.data,{payload:data});await page.goto(origin+'/workflow-runs/'+run.id);await page.getByRole('heading',{name:'Run details',exact:true}).waitFor();await page.locator('.wf-node-detail').first().locator('summary').first().click();await pair('run-detail',async lang=>{await page.getByRole('heading',{name:lang==='en'?'Run details':'运行详情',exact:true}).waitFor();await page.getByText(lang==='en'?'No files produced':'未生成文件',{exact:true}).waitFor();const formatted=await page.evaluate(({lang,stamp})=>new Intl.DateTimeFormat(lang,{dateStyle:'medium',timeStyle:'short'}).format(new Date(stamp)),{lang,stamp:run.created_at});assert.ok((await page.locator('.wf-run-summary').textContent()).includes(formatted));assert.ok((await page.locator('.wf-node-detail').first().textContent()).includes('Authored 日志 must stay literal'));assert.ok((await page.locator('.wf-node-detail').first().textContent()).includes('literal English 数据'));assert.ok((await page.locator('.wf-run-summary .badge').textContent()).includes(lang==='en'?'Succeeded':'成功'));});
 const after=await request('/api/workflows/'+w.id);assert.deepEqual(after.nodes,saved.nodes);assert.deepEqual(after.edges,saved.edges);assert.deepEqual(after.params,saved.params);const final=await request('/api/workflow-runs/'+run.id);assert.deepEqual(final.nodes.a.output.data,run.nodes.a.output.data);assert.deepEqual(final.nodes.b.inputs,run.nodes.b.inputs);console.log(JSON.stringify({run:run.id}));
 """)
    assert len(durable_runs(tmp_path))==1


def test_browser_chinese_self_cycle_port_errors_and_picker_prevention_preserve_history(isolated_url,tmp_path):
    browser(isolated_url,'const imageDir='+json.dumps(str(evidence_directory(tmp_path)))+';const bundle="cycles";'+EVIDENCE+r"""
 const w=await seed({nodes:[node('a'),node('b'),node('c')],edges:[{source:'a',target:'b'},{source:'b',target:'c'}]});await open(w);await page.locator('article[data-node-id="b"]').click();await page.getByLabel('Name',{exact:true}).last().fill('之前的有效编辑');const expected=await save(w);await locale('zh-CN');
 const saveZh=async()=>{const response=page.waitForResponse(r=>r.url().endsWith('/api/workflows/'+w.id)&&r.request().method()==='PUT');await page.getByRole('button',{name:'保存草稿',exact:true}).click();assert.equal((await response).status(),200);return request('/api/workflows/'+w.id);};
 for(const [from,to] of [['a','a'],['c','a']]){const a=await page.locator('article[data-node-id="'+from+'"] .output-port').boundingBox(),b=await page.locator('article[data-node-id="'+to+'"] .input-port').boundingBox();await page.mouse.move(a.x+a.width/2,a.y+a.height/2);await page.mouse.down();await page.mouse.move(b.x+b.width/2,b.y+b.height/2,{steps:8});await page.mouse.up();await page.getByText('该连接会形成循环。 '+from+' → '+to,{exact:true}).waitFor();await capture('cycle-'+from+'-'+to);const saved=await saveZh();assert.deepEqual(saved.nodes,expected.nodes);assert.deepEqual(saved.edges,expected.edges);}
 await page.locator('article[data-node-id="a"]').click();await page.getByRole('button',{name:'输入',exact:true}).click();await page.getByRole('button',{name:'添加输入映射',exact:true}).click();await page.getByLabel('来源',{exact:true}).selectOption('node');assert.deepEqual(await page.getByLabel('上游输出',{exact:true}).locator('option').evaluateAll(xs=>xs.map(x=>x.value)),['']);await capture('cycle-picker-prevention');await page.getByRole('button',{name:'关闭',exact:true}).last().click();
 await page.getByRole('button',{name:'撤销',exact:true}).click();let changed=await saveZh();assert.equal(changed.nodes.find(n=>n.id==='b').name,'b');assert.deepEqual(changed.edges,expected.edges);await page.getByRole('button',{name:'重做',exact:true}).click();changed=await saveZh();assert.deepEqual(changed.nodes,expected.nodes);assert.deepEqual(changed.edges,expected.edges);assert.deepEqual(await request('/api/workflow-runs'),[]);
 """)


def test_browser_invalid_json_message_switches_language_without_replacing_draft(isolated_url,tmp_path):
    browser(isolated_url,'const imageDir='+json.dumps(str(evidence_directory(tmp_path)))+';const bundle="validation";'+EVIDENCE+r"""
 const w=await seed({nodes:[node('a')],edges:[],params:{preserve:'中文 English'}});await open(w);await page.getByRole('button',{name:'Settings',exact:true}).click();const before=await request('/api/workflows/'+w.id);const field=page.getByLabel('Workflow parameters (JSON)',{exact:true});await field.fill('{"unfinished":');assert.equal(await field.evaluate(n=>n.validationMessage),'Enter valid JSON.');await field.evaluate(n=>n.reportValidity());await capture('json-error');await locale('zh-CN');const translated=page.getByLabel('工作流参数（JSON）',{exact:true});assert.equal(await translated.inputValue(),'{"unfinished":');assert.equal(await translated.evaluate(n=>n.validationMessage),'请输入有效的 JSON。');await translated.evaluate(n=>n.reportValidity());await capture('json-error');await locale('en');assert.equal(await field.evaluate(n=>n.validationMessage),'Enter valid JSON.');await field.fill(JSON.stringify(before.params));assert.equal(await field.evaluate(n=>n.validationMessage),'');await locale('zh-CN');assert.equal(await translated.evaluate(n=>n.validationMessage),'');assert.deepEqual((await request('/api/workflows/'+w.id)).params,before.params);
 """)


@pytest.mark.parametrize('surface',['mapping','weekdays'])
def test_browser_existing_mapping_label_and_weekdays_localize_in_place(isolated_url,surface):
    browser(isolated_url,'const surface='+json.dumps(surface)+';'+r"""
 const w=await seed({params:{payload:'Keep literal'},nodes:[node('a',{inputs:{payload:{source:'parameter',path:['payload']}}})],edges:[]});await open(w);
 if(surface==='mapping'){await page.locator('article[data-node-id="a"]').click();await page.getByRole('button',{name:'Inputs',exact:true}).click();assert.equal(await page.locator('.wf-binding small').first().textContent(),'Workflow parameter → payload');await page.locator('[data-locale="zh-CN"]').click();assert.equal(await page.locator('.wf-binding small').first().textContent(),'工作流参数 → payload');await page.locator('[data-locale="en"]').click();assert.equal(await page.locator('.wf-binding small').first().textContent(),'Workflow parameter → payload');}
 else{await page.getByRole('button',{name:'Triggers',exact:true}).click();await page.getByLabel('Schedule',{exact:true}).selectOption('weekly');await page.locator('[data-locale="zh-CN"]').click();const lines=await page.locator('.wf-weekdays label span').evaluateAll(xs=>xs.map(x=>{const range=document.createRange();range.selectNodeContents(x);return range.getClientRects().length;}));assert.deepEqual(lines,[1,1,1,1,1,1,1]);}
 assert.deepEqual((await request('/api/workflows/'+w.id)).params,{payload:'Keep literal'});
 """)


def test_browser_run_list_and_schedule_history_timestamps_switch_without_changing_instants(isolated_url,tmp_path):
    from datetime import datetime, timezone, timedelta
    from taskconsole.store import Store
    from taskconsole.workflows import WorkflowService
    store=Store(tmp_path,'sqlite:///'+str(tmp_path/'browser.sqlite'))
    svc=WorkflowService(store)
    w=svc.save({'name':'Locale history fixture','timezone':'America/Chicago','nodes':[{'id':'a','kind':'python','source':'def main(inputs): return {}','inputs':{},'config':{}}],'edges':[]})
    svc.publish(w['id'])
    start=datetime(2026,9,21,12,0,tzinfo=timezone.utc)
    svc.save({**w,'enabled':True,'schedule':{'kind':'interval','every':1,'anchor':start.isoformat()}},w['id'])
    svc.tick(start);svc.tick(start+timedelta(minutes=5))
    try:
        browser(isolated_url,'const id='+json.dumps(w['id'])+';const imageDir='+json.dumps(str(evidence_directory(tmp_path)))+';const bundle="times";'+EVIDENCE+r"""
 const w=await request('/api/workflows/'+id),history=await request('/api/workflows/'+id+'/schedule-history'),runs=await request('/api/workflow-runs?workflow_id='+id);assert.ok(history.items.some(x=>x.kind==='gap'));await open(w);await page.getByRole('button',{name:'Runs',exact:true}).click();await page.locator('[data-history-id]').first().waitFor();const apiReads=[];page.on('request',r=>{if(r.method()==='GET'&&/\/api\/workflow/.test(r.url()))apiReads.push(r.url());});
 for(const lang of ['en','zh-CN','en']){await locale(lang);for(const item of history.items){const row=page.locator('[data-history-id="'+item.id+'"]');for(const stamp of [item.from,item.until,item.occurrence].filter(Boolean)){const expected=await page.evaluate(({stamp,lang})=>new Intl.DateTimeFormat(lang,{dateStyle:'medium',timeStyle:'long',timeZone:'America/Chicago'}).format(new Date(stamp)),{stamp,lang});const time=row.locator('time[datetime="'+stamp+'"]');assert.equal(await time.textContent(),expected);assert.equal(await time.getAttribute('datetime'),stamp);}}for(const run of runs){const expected=await page.evaluate(({stamp,lang})=>new Intl.DateTimeFormat(lang,{dateStyle:'medium',timeStyle:'short'}).format(new Date(stamp)),{stamp:run.created_at,lang});const row=page.locator('tr').filter({has:page.getByRole('button',{name:run.id.slice(0,12),exact:true})});assert.ok((await row.textContent()).includes(expected));}await capture('run-list-history');}assert.deepEqual(apiReads,[]);assert.deepEqual((await request('/api/workflows/'+id+'/schedule-history')).items,history.items);assert.deepEqual(await request('/api/workflow-runs?workflow_id='+id),runs);
 // Malformed/missing transport metadata is a rendering fixture, not execution evidence.
 let missing=false;await page.route('**/api/workflow-runs',async route=>{const response=await route.fetch();const values=await response.json();values[0].created_at=missing?null:'invalid-date';await route.fulfill({response,json:values});});await page.goto(origin+'/workflow-runs');await page.locator('tbody tr').first().waitFor();assert.equal(await page.locator('tbody tr').first().locator('td').nth(3).textContent(),'—');await locale('zh-CN');assert.equal(await page.locator('tbody tr').first().locator('td').nth(3).textContent(),'—');missing=true;await page.goto(origin+'/workflow-runs');await page.locator('tbody tr').first().waitFor();assert.equal(await page.locator('tbody tr').first().locator('td').nth(3).textContent(),'—');await locale('en');assert.equal(await page.locator('tbody tr').first().locator('td').nth(3).textContent(),'—');assert.deepEqual(await request('/api/workflows/'+id+'/schedule-history'),history);
 """)
    finally:
        store.engine.dispose()


def test_browser_builtin_catalog_copy_localizes_by_id_without_changing_clone_or_custom_template(isolated_url):
    browser(isolated_url,r"""
 const original=(await request('/api/workflow-templates'))[0];assert.equal(original.id,'morning-report');await page.route('**/api/workflow-templates',async route=>{const response=await route.fetch();const templates=await response.json();await route.fulfill({response,json:[...templates,{...templates[0],id:'user-authored-template',name:'Morning report',description:'Authored English 描述'}]});});await page.goto(origin+'/templates');await page.locator('.wf-template').nth(1).waitFor();await page.locator('[data-locale="zh-CN"]').click();assert.equal(await page.locator('.wf-template').first().locator('h2').textContent(),'晨间报告');assert.equal(await page.locator('.wf-template').first().locator('p').textContent(),'示例 SQLite 订单 → Python 汇总 → JavaScript 报告，无需外部账户。');assert.equal(await page.locator('.wf-template').nth(1).locator('h2').textContent(),'Morning report');assert.equal(await page.locator('.wf-template').nth(1).locator('p').textContent(),'Authored English 描述');await page.locator('[data-locale="en"]').click();assert.equal(await page.locator('.wf-template').first().locator('h2').textContent(),original.name);await page.locator('[data-locale="zh-CN"]').click();const response=page.waitForResponse(r=>r.url().endsWith('/api/workflows')&&r.request().method()==='POST');await page.locator('.wf-template').first().getByRole('button',{name:'打开可视化编辑器',exact:true}).click();const copy=await(await response).json();assert.equal(copy.name,original.name);assert.equal(copy.description,original.description);assert.deepEqual(copy.nodes,original.nodes);assert.deepEqual(copy.edges,original.edges);await page.unroute('**/api/workflow-templates');assert.deepEqual((await request('/api/workflow-templates'))[0],original);
 """)
