"""Exact mapping acceptance through isolated Chromium and the actual n8n worker."""
import json
import time
from pathlib import Path

import pytest

from test_acceptance_browser import browser, engine_url, isolated_url, pytestmark


HELPERS = r"""
const selectNode=async id=>{await page.getByRole('button',{name:'Editor',exact:true}).click();await page.getByRole('button',{name:'Fit canvas',exact:true}).click();await page.locator('article[data-node-id="'+id+'"]').click();};
const mapping=async(target,key,kind,value,{source,path,optional=false,fallback=null,type=''}={})=>{
 await selectNode(target);await page.getByRole('button',{name:'Inputs',exact:true}).click();await page.getByRole('button',{name:'Add input mapping',exact:true}).click();await page.getByLabel('Target field',{exact:true}).fill(key);await page.getByLabel('Source',{exact:true}).selectOption(kind);
 if(kind==='constant')await page.getByLabel('Value (JSON or text)',{exact:true}).fill(JSON.stringify(value));
 if(kind==='parameter')await page.getByLabel('Workflow parameter',{exact:true}).selectOption(JSON.stringify([value]));
 if(kind==='node'){await page.getByLabel('Upstream output',{exact:true}).selectOption(source);await page.getByLabel('Output field',{exact:true}).selectOption(JSON.stringify([path]));}
 await page.getByLabel('Expected type',{exact:true}).selectOption(type);if(optional){await page.getByLabel('Optional field with explicit fallback',{exact:true}).check();await page.getByLabel('Fallback value (JSON)',{exact:true}).fill(JSON.stringify(fallback));}
 await page.getByRole('button',{name:'Map input',exact:true}).click();
};
const waitRun=async run=>{let result;const deadline=Date.now()+90000;do{result=await request('/api/workflow-runs/'+run.id);if(!['queued','running','cancelling'].includes(result.status))break;await page.waitForTimeout(150);}while(Date.now()<deadline);assert.equal(result.engine,'n8n');return result;};
const testRun=async w=>{const response=page.waitForResponse(r=>r.url().endsWith('/api/workflows/'+w.id+'/run'));await page.getByRole('button',{name:'Test workflow',exact:true}).click();const r=await response;assert.equal(r.status(),202,await r.text());return waitRun(await r.json());};
const sourceCode=async(id,text)=>{await selectNode(id);await page.getByLabel('Source code',{exact:true}).fill(text);};
"""


def counted_source(path, expression='inputs'):
    return ('from pathlib import Path\n'
            'def main(inputs):\n'
            f'    with Path({str(path)!r}).open("a") as marker: marker.write("invoked\\n")\n'
            f'    return {expression}\n')


def count(path):
    return Path(path).read_text().splitlines() if Path(path).exists() else []


def durable_runs(tmp_path):
    from taskconsole.store import Store
    store=Store(tmp_path, 'sqlite:///' + str(tmp_path / 'browser.sqlite'))
    deadline=time.monotonic()+10
    while True:
        with store.transaction() as tx:
            records=tx.all('workflow_run')
        # The finish callback precedes the CLI adapter's durable execution-ID
        # capture. Keep the worker alive until that final evidence is committed.
        if records and all(r.get('n8n_execution_id') for r in records):
            return records
        if time.monotonic()>=deadline:
            pytest.fail('Actual n8n execution identity was not persisted after terminal callbacks')
        time.sleep(.05)


def test_browser_no_upstream_mapping_keeps_order_but_never_injects_data(engine_url, tmp_path):
    counter = tmp_path / 'consumer-counter.txt'
    browser(engine_url, HELPERS + 'const counted='+json.dumps(counted_source(counter))+';'+r"""
 const {id,...template}=(await request('/api/workflow-templates'))[0];template.nodes=template.nodes.filter(n=>n.id!=='report');template.edges=template.edges.filter(e=>e.target!=='report');const consumer=template.nodes.find(n=>n.id==='summary');consumer.source=counted;consumer.outputs={};const w=await seed(template);await open(w);
 await selectNode('summary');await page.getByRole('button',{name:'Inputs',exact:true}).click();await page.locator('.wf-binding').filter({has:page.getByText('orders',{exact:true})}).getByRole('button',{name:'×',exact:true}).click();assert.ok((await page.locator('.wf-no-input').textContent()).includes('this node receives {}'));
 let run=await testRun(w);assert.equal(run.status,'succeeded');assert.deepEqual(run.nodes.summary.inputs,{});assert.deepEqual(run.nodes.summary.output.data,{});assert.ok(run.nodes.orders.output.data.rows.length);assert.ok(Date.parse(run.nodes.summary.started_at)>=Date.parse(run.nodes.orders.finished_at));
 await mapping('summary','message','constant','hello');run=await testRun(w);assert.equal(run.status,'succeeded');assert.deepEqual(run.nodes.summary.inputs,{message:'hello'});assert.deepEqual(run.nodes.summary.output.data,{message:'hello'});assert.ok((await request('/api/workflows/'+w.id)).edges.some(e=>e.source==='orders'&&e.target==='summary'));assert.ok(Object.values((await request('/api/workflows/'+w.id)).nodes.find(n=>n.id==='summary').inputs).every(b=>!b.optional));
 await sourceCode('orders','WITH RECURSIVE slow(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM slow WHERE n<2000000) SELECT max(n) AS sentinel FROM slow');run=await testRun(w);assert.equal(run.status,'succeeded');assert.equal(run.nodes.orders.output.data.rows[0].sentinel,2000000);assert.ok(Date.parse(run.nodes.summary.started_at)>=Date.parse(run.nodes.orders.finished_at));assert.deepEqual(run.nodes.summary.inputs,{message:'hello'});
 await sourceCode('orders','SELECT * FROM nonexistent_mapping_fixture');run=await testRun(w);assert.equal(run.status,'failed');assert.equal(run.nodes.orders.status,'failed');assert.ok(['skipped','not_run'].includes(run.nodes.summary.status));assert.equal(run.nodes.summary.attempts.length,0);
 await page.getByRole('button',{name:'Fit canvas',exact:true}).click();const sqlName=w.nodes.find(n=>n.id==='orders').name;await page.locator('.wf-edge-hit[aria-label="'+sqlName+' → '+consumer.name+'"]').press('Enter');await page.getByRole('button',{name:'Remove connection',exact:true}).click();run=await testRun(w);assert.equal(run.nodes.summary.status,'succeeded');assert.deepEqual(run.nodes.summary.inputs,{message:'hello'});assert.deepEqual((await request('/api/workflows/'+w.id)).edges,[]);
 """)
    assert count(counter) == ['invoked'] * 4
    records=durable_runs(tmp_path)
    assert len(records)==5
    assert sum(not r['snapshot']['edges'] for r in records)==1


def test_browser_independent_nodes_do_not_inherit_visual_neighbor_data(engine_url, tmp_path):
    counters={key:tmp_path/(key+'-counter.txt') for key in ('a','b','c')}
    sources={key:counted_source(path, "{'token':'must-not-leak'}" if key=='a' else "{'received':inputs,'marker':"+repr(key)+"}") for key,path in counters.items()}
    browser(engine_url, HELPERS+'const sources='+json.dumps(sources)+';'+r"""
 const w=await seed({nodes:[node('a',{source:sources.a}),node('b',{source:sources.b}),node('c',{source:sources.c})],edges:[]});await open(w);await mapping('b','label','constant','independent');const saved=await save(w);assert.deepEqual(saved.edges,[]);const run=await testRun(w);assert.equal(run.status,'succeeded');assert.deepEqual((await request('/api/workflows/'+w.id)).edges,[]);assert.deepEqual(run.nodes.b.inputs,{label:'independent'});assert.deepEqual(run.nodes.b.output.data,{received:{label:'independent'},marker:'b'});assert.deepEqual(run.nodes.a.output.data,{token:'must-not-leak'});await page.goto(origin+'/workflow-runs/'+run.id);assert.ok((await page.locator('body').textContent()).includes('independent'));
 """)
    assert {key:count(path) for key,path in counters.items()} == {'a':['invoked'],'b':['invoked'],'c':['invoked']}
    assert durable_runs(tmp_path)[0]['snapshot']['edges']==[]


def test_browser_optional_missing_field_and_existing_empty_array_are_distinct(engine_url, tmp_path):
    counter=tmp_path/'optional-counter.txt'
    browser(engine_url, HELPERS+'const counted='+json.dumps(counted_source(counter))+';'+r"""
 const w=await seed({nodes:[node('source',{source:'def main(inputs): return {"rows": ["stale-must-not-leak"], "empty": []}',outputs:{type:'object',properties:{rows:{type:'array'},empty:{type:'array'}}}}),node('consumer',{source:counted})],edges:[]});await open(w);
 await mapping('consumer','orders','node',null,{source:'source',path:'rows',optional:true,fallback:[],type:'array'});await mapping('consumer','actual_empty','node',null,{source:'source',path:'empty',optional:true,fallback:['wrong'],type:'array'});let saved=await save(w);assert.deepEqual(saved.nodes.find(n=>n.id==='consumer').inputs.orders,{source:'node',node_id:'source',path:['rows'],optional:true,default:[],type:'array'});await open(w);await selectNode('consumer');await page.getByRole('button',{name:'Inputs',exact:true}).click();assert.equal(await page.getByText('Optional',{exact:true}).count(),2);const prior=await testRun(w);assert.equal(prior.status,'succeeded');assert.deepEqual(prior.nodes.consumer.inputs,{orders:['stale-must-not-leak'],actual_empty:[]});await sourceCode('source','def main(inputs): return {"empty": []}');await selectNode('consumer');await page.getByRole('button',{name:'Inputs',exact:true}).click();await page.getByText('Stale sample — draft changed',{exact:true}).waitFor();
 const run=await testRun(w);assert.notEqual(run.id,prior.id);assert.equal(run.status,'succeeded');assert.deepEqual(run.nodes.consumer.inputs,{orders:[],actual_empty:[]});assert.deepEqual(run.nodes.source.output.data,{empty:[]});assert.equal(run.nodes.consumer.input_provenance.orders.default_used,true);assert.equal(run.nodes.consumer.input_provenance.actual_empty.default_used,false);await page.locator('.wf-drawer-head').getByText('Succeeded',{exact:true}).waitFor();await selectNode('consumer');await page.getByRole('button',{name:'Inputs',exact:true}).click();assert.ok((await page.locator('.wf-inspector').textContent()).includes(run.id));assert.ok((await page.locator('.wf-binding').filter({has:page.getByText('orders',{exact:true})}).textContent()).includes('Sample used explicit fallback'));assert.ok((await page.locator('.wf-binding').filter({has:page.getByText('actual_empty',{exact:true})}).textContent()).includes('Sample used the source value'));const sample=await request('/api/workflow-runs/'+run.id+'/nodes/consumer/sample');assert.deepEqual(sample.inputs,{orders:[],actual_empty:[]});assert.equal(sample.run_id,run.id);await page.locator('[data-locale="zh-CN"]').click();assert.equal(await page.getByText('此样本使用了明确默认值',{exact:true}).count(),1);assert.equal(await page.getByText('此样本使用了来源值',{exact:true}).count(),1);await page.locator('[data-locale="en"]').click();
 """)
    assert count(counter)==['invoked']*2
    assert len(durable_runs(tmp_path))==2


def test_browser_present_false_zero_null_and_empty_values_never_use_fallback(engine_url, tmp_path):
    counter=tmp_path/'values-counter.txt'
    browser(engine_url, HELPERS+'const counted='+json.dumps(counted_source(counter))+';'+r"""
 const values={array:[],object:{},nullable:null,zero:0,disabled:false,text:''};const schema={type:'object',properties:Object.fromEntries(Object.keys(values).map(key=>[key,{}]))};const w=await seed({nodes:[node('source',{kind:'javascript',source:'async function main(inputs) { return '+JSON.stringify(values)+'; }',outputs:schema}),node('consumer',{source:counted})],edges:[]});await open(w);
 for(const key of Object.keys(values))await mapping('consumer',key,'node',null,{source:'source',path:key,optional:true,fallback:'wrong fallback'});await save(w);await open(w);const run=await testRun(w);assert.equal(run.status,'succeeded');assert.deepEqual(run.nodes.consumer.inputs,values);assert.deepEqual(run.nodes.consumer.output.data,values);assert.ok(Object.values((await request('/api/workflows/'+w.id)).nodes.find(n=>n.id==='consumer').inputs).every(v=>v.optional&&v.default==='wrong fallback'));
 """)
    assert count(counter)==['invoked']
    assert len(durable_runs(tmp_path))==1


def test_browser_required_missing_output_fails_before_consumer_side_effect(engine_url, tmp_path):
    counter=tmp_path/'required-counter.txt'
    browser(engine_url, HELPERS+'const counted='+json.dumps(counted_source(counter))+';'+r"""
 const w=await seed({nodes:[node('source',{source:'def main(inputs): return {}',outputs:{type:'object',properties:{summary:{type:'object'}}}}),node('consumer',{source:counted})],edges:[]});await open(w);await mapping('consumer','summary','node',null,{source:'source',path:'summary'});await save(w);const run=await testRun(w);assert.equal(run.status,'failed');assert.equal(run.nodes.source.status,'succeeded');assert.equal(run.nodes.consumer.status,'failed');assert.equal(run.nodes.consumer.attempts.length,0);assert.match(run.nodes.consumer.error,/summary/);assert.match(run.nodes.consumer.error,/source/);assert.deepEqual(run.nodes.consumer.inputs,{});await page.goto(origin+'/workflow-runs/'+run.id);const failed=page.locator('.wf-node-detail').filter({has:page.getByText('consumer',{exact:true})});assert.ok((await failed.textContent()).includes('Missing required input'));assert.ok((await failed.textContent()).includes('summary'));
 """)
    assert count(counter)==[]
    assert len(durable_runs(tmp_path))==1


def test_browser_constants_parameters_and_sql_output_coexist_with_override(engine_url, tmp_path):
    counter=tmp_path/'mixed-counter.txt'
    browser(engine_url, HELPERS+'const suffix='+json.dumps('\n_original_main=main\n'+counted_source(counter,'_original_main(inputs)'))+';'+r"""
 const {id,...template}=(await request('/api/workflow-templates'))[0];const target=template.nodes.find(n=>n.id==='summary');target.inputs={};target.source+=suffix;template.edges=template.edges.filter(e=>e.source!=='orders');template.params={region:'East',threshold:20};const w=await seed(template);await open(w);
 await mapping('summary','orders','node',null,{source:'orders',path:'rows'});await mapping('summary','region','parameter','region');await mapping('summary','limit','constant',2);await mapping('summary','enabled','constant',false);await mapping('summary','tags','constant',['demo']);let saved=await save(w);const bindings=saved.nodes.find(n=>n.id==='summary').inputs;assert.equal(Object.keys(bindings).length,5);await open(w);await selectNode('summary');await page.getByRole('button',{name:'Inputs',exact:true}).click();assert.equal(await page.locator('.wf-binding').count(),5);const publication=await request('/api/workflows/'+w.id+'/publish','POST',{});const run=await waitRun(await request('/api/workflows/'+w.id+'/run','POST',{params:{region:'West',threshold:25},idempotency_key:'mixed-once'}));assert.equal(run.status,'succeeded');assert.equal(run.version_id,publication.version_id);const expected={orders:[{order_id:'A001',amount:'10.50',region:'华东'},{order_id:'A002',amount:'20.25',region:null},{order_id:'A003',amount:'0.00',region:'西部'}],region:'West',limit:2,enabled:false,tags:['demo']};assert.deepEqual(run.nodes.summary.inputs,expected);assert.deepEqual(run.nodes.summary.output.data,{summary:{count:3,total:'30.75'}});assert.deepEqual(run.nodes.report.inputs,{summary:{count:3,total:'30.75'}});assert.equal(run.nodes.report.status,'succeeded');assert.equal(Object.hasOwn(run.nodes.summary.inputs,'threshold'),false);assert.deepEqual(run.params,{region:'West',threshold:25});await page.goto(origin+'/workflow-runs/'+run.id);assert.ok((await page.locator('body').textContent()).includes('West'));
 """)
    assert count(counter)==['invoked']
    assert len(durable_runs(tmp_path))==1


def test_browser_type_invalid_fallback_and_runtime_null_never_execute_consumer(engine_url, tmp_path):
    counter=tmp_path/'type-counter.txt'
    browser(engine_url, HELPERS+'const counted='+json.dumps(counted_source(counter))+';'+r"""
 const {id,...template}=(await request('/api/workflow-templates'))[0];template.nodes=template.nodes.filter(n=>n.id!=='report');const source=template.nodes.find(n=>n.id==='orders');source.outputs.properties.rowCount={type:'integer'};const consumer=template.nodes.find(n=>n.id==='summary');consumer.inputs={};consumer.source=counted;consumer.outputs={};consumer.input_schema={type:'object',properties:{orders:{type:'array'}}};template.edges=[];const w=await seed(template);await open(w);await mapping('summary','orders','node',null,{source:'orders',path:'rowCount',type:'array'});let saved=await save(w);assert.ok(saved.validation_errors.some(e=>e.node_id==='summary'&&e.field==='orders'));const rejected=page.waitForResponse(r=>r.url().endsWith('/publish'));await page.getByRole('button',{name:'Publish',exact:true}).click();assert.equal((await rejected).status(),422);const typeError=await page.locator('.wf-validation').textContent();assert.ok(typeError.includes('incompatible'));assert.ok(typeError.includes('integer'));assert.ok(typeError.includes('array'));assert.ok(typeError.includes('orders'));const testRejected=page.waitForResponse(r=>r.url().endsWith('/run'));await page.getByRole('button',{name:'Test workflow',exact:true}).click();assert.equal((await testRejected).status(),422);assert.deepEqual(await request('/api/workflow-runs'),[]);
 await selectNode('summary');await page.getByRole('button',{name:'Inputs',exact:true}).click();await page.locator('.wf-binding').getByRole('button',{name:'×',exact:true}).click();await mapping('summary','orders','node',null,{source:'orders',path:'rows',type:'array',optional:true,fallback:'not an array'});saved=await save(w);assert.ok(saved.validation_errors.some(e=>e.node_id==='summary'&&e.field==='orders'));const badDefault=page.waitForResponse(r=>r.url().endsWith('/publish'));await page.getByRole('button',{name:'Publish',exact:true}).click();assert.equal((await badDefault).status(),422);
 const uncertain=await seed({nodes:[node('source',{source:'def main(inputs): return {"rows":None}',outputs:{type:'object',properties:{rows:{}}}}),node('consumer',{source:counted})],edges:[]});await open(uncertain);await mapping('consumer','orders','node',null,{source:'source',path:'rows',type:'array',optional:true,fallback:[]});saved=await save(uncertain);assert.equal(saved.validation_errors.length,0);const run=await testRun(uncertain);assert.equal(run.status,'failed');assert.equal(run.nodes.source.status,'succeeded');assert.equal(run.nodes.source.output.data.rows,null);assert.equal(run.nodes.consumer.status,'failed');assert.equal(run.nodes.consumer.attempts.length,0);assert.match(run.nodes.consumer.error,/orders.*array|array.*orders/);assert.deepEqual(run.nodes.consumer.inputs,{});await page.goto(origin+'/workflow-runs/'+run.id);assert.ok((await page.locator('body').textContent()).includes('array'));
 """)
    assert count(counter)==[]
    assert len(durable_runs(tmp_path))==1


def test_browser_unicode_duplicate_names_keep_ids_and_prior_run_snapshot(engine_url, tmp_path):
    python_counter=tmp_path/'rename-python-counter.txt'
    js_counter=tmp_path/'rename-js-counter.txt'
    python_suffix='\n_original_main=main\n'+counted_source(python_counter, '_original_main(inputs)')
    js_prefix='require("fs").appendFileSync('+json.dumps(str(js_counter))+',"invoked\\n");\n'
    result=browser(engine_url, HELPERS+'const pySuffix='+json.dumps(python_suffix)+';const jsPrefix='+json.dumps(js_prefix)+';'+r"""
 const {id,...template}=(await request('/api/workflow-templates'))[0];template.nodes.find(n=>n.id==='summary').source+=pySuffix;template.nodes.find(n=>n.id==='report').source=jsPrefix+template.nodes.find(n=>n.id==='report').source;const w=await seed(template);const published=await request('/api/workflows/'+w.id+'/publish','POST',{});const prior=await waitRun(await request('/api/workflows/'+w.id+'/run','POST',{idempotency_key:'before-rename'}));assert.equal(prior.status,'succeeded');await open(w);const duplicateName='数据源 — same display';for(const id of ['orders','summary']){await selectNode(id);await page.getByLabel('Name',{exact:true}).last().fill(duplicateName);}const saved=await save(w);assert.deepEqual(saved.nodes.map(n=>n.id),w.nodes.map(n=>n.id));assert.equal(saved.nodes.find(n=>n.id==='summary').inputs.orders.node_id,'orders');assert.equal(saved.nodes.find(n=>n.id==='report').inputs.summary.node_id,'summary');assert.equal(saved.published_version_id,published.version_id);
 await selectNode('report');await page.getByRole('button',{name:'Inputs',exact:true}).click();assert.ok((await page.locator('.wf-binding').textContent()).includes(duplicateName));await page.locator('.wf-binding').getByRole('button',{name:'Configuration',exact:true}).click();const labels=await page.getByLabel('Upstream output',{exact:true}).locator('option').allTextContents();assert.ok(labels.includes(duplicateName+' · orders'));assert.ok(labels.includes(duplicateName+' · summar'));await page.locator('.wf-binding-form').getByRole('button',{name:'Close',exact:true}).click();const run=await testRun(w);assert.equal(run.status,'succeeded');assert.deepEqual(run.nodes.summary.output.data.summary,{count:3,total:'30.75'});assert.deepEqual(run.nodes.report.inputs,{summary:{count:3,total:'30.75'}});await page.goto(origin+'/workflow-runs/'+prior.id);const content=await page.locator('body').textContent();assert.ok(content.includes('Order query'));assert.ok(content.includes('Order summary'));assert.equal(content.includes(duplicateName),false);console.log(JSON.stringify({prior:prior.id,current:run.id,version:published.version_id}));
 """)
    ids=json.loads(result)
    from taskconsole.store import Store
    with Store(tmp_path,'sqlite:///'+str(tmp_path/'browser.sqlite')).transaction() as tx:
        prior=tx.get('workflow_run',ids['prior'])
        current=tx.get('workflow_run',ids['current'])
        published=tx.get('workflow_version',ids['version'])
    assert [n['name'] for n in prior['snapshot']['nodes']]==['Order query','Order summary','Report file']
    assert published['snapshot']['nodes']==prior['snapshot']['nodes']
    assert current['snapshot']['nodes'][0]['name']==current['snapshot']['nodes'][1]['name']=='数据源 — same display'
    assert count(python_counter)==['invoked']*2
    assert count(js_counter)==['invoked']*2


def test_browser_declared_scalar_source_cannot_publish_object_field_binding(isolated_url):
    browser(isolated_url, HELPERS+r"""
 const w=await seed({nodes:[node('source',{outputs:{type:'object',properties:{summary:{type:'object'}}}}),node('consumer')],edges:[]});await open(w);await mapping('consumer','summary','node',null,{source:'source',path:'summary'});await selectNode('source');await page.getByRole('button',{name:'Outputs',exact:true}).click();await page.getByLabel('Output contract (JSON Schema)',{exact:true}).fill('{"type":"string"}');await page.getByRole('button',{name:'Configuration',exact:true}).first().click();await page.getByLabel('Source code',{exact:true}).fill('def main(inputs): return "scalar"');await save(w);const response=page.waitForResponse(r=>r.url().endsWith('/publish'));await page.getByRole('button',{name:'Publish',exact:true}).click();assert.equal((await response).status(),422);assert.ok((await page.locator('.wf-validation').textContent()).includes('summary'));await page.locator('.wf-validation button').filter({hasText:'consumer'}).first().click();assert.ok((await page.locator('article[data-node-id="consumer"]').getAttribute('class')).includes('selected'));assert.equal((await request('/api/workflows/'+w.id)).published_version_id,null);
 """)


def test_browser_nonadjacent_sql_source_is_selected_by_id_not_neighbor(engine_url, tmp_path):
    consumer_counter=tmp_path/'nonadjacent-consumer.txt'
    other_counter=tmp_path/'nonadjacent-other.txt'
    other_source='async function main(inputs) { require("fs").appendFileSync('+json.dumps(str(other_counter))+',"invoked\\n"); return {rows:["must-not-leak"]}; }'
    browser(engine_url,HELPERS+'const consumerCode='+json.dumps(counted_source(consumer_counter))+';const otherCode='+json.dumps(other_source)+';'+r"""
 const sql=(await request('/api/workflow-templates'))[0].nodes.find(n=>n.id==='orders');const w=await seed({nodes:[node('c',{source:consumerCode}),node('b',{kind:'javascript',source:otherCode,outputs:{type:'object',properties:{rows:{type:'array'}}}}),sql],edges:[]});await open(w);await mapping('c','orders','node',null,{source:'orders',path:'rows',type:'array'});const saved=await save(w);assert.deepEqual(saved.edges,[{source:'orders',target:'c'}]);assert.equal(saved.nodes.find(n=>n.id==='c').inputs.orders.node_id,'orders');const run=await testRun(w);assert.equal(run.status,'succeeded');assert.deepEqual(run.nodes.c.inputs,{orders:[{order_id:'A001',amount:'10.50',region:'华东'},{order_id:'A002',amount:'20.25',region:null},{order_id:'A003',amount:'0.00',region:'西部'}]});assert.deepEqual(run.nodes.b.inputs,{});assert.deepEqual(run.nodes.b.output.data,{rows:['must-not-leak']});assert.equal(run.nodes.orders.attempts.length,1);
 """)
    assert count(consumer_counter)==['invoked']
    assert count(other_counter)==['invoked']
    assert durable_runs(tmp_path)[0]['snapshot']['edges']==[{'source':'orders','target':'c'}]
