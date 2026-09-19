"""Exact remaining dataflow browser oracles; synthetic isolated state only."""
import json
import subprocess
import sys

import pytest

from test_acceptance_browser import browser, engine_url, isolated_url as base_isolated_url, pytestmark
from test_acceptance_browser_mapping import HELPERS, count, counted_source, durable_runs


@pytest.fixture
def isolated_url(tmp_path, monkeypatch):
    """Register a test-only SQL invocation witness in the actual callback server.

    The witness is invoked once by a materialized CTE, not once per returned row.
    It observes query invocation without adding a production SQL write operation.
    """
    original = subprocess.Popen
    marker = str(tmp_path / 'sql-invocations.txt')
    bootstrap = f'''
import sqlite3, runpy, sys
from pathlib import Path
original_connect = sqlite3.connect
def witnessed_connect(*args, **kwargs):
    connection = original_connect(*args, **kwargs)
    def invocation():
        with Path({marker!r}).open('a') as marker: marker.write('invoked\\n')
        return 1
    connection.create_function('acceptance_invocation', 0, invocation)
    return connection
sqlite3.connect = witnessed_connect
sys.argv = ['taskconsole', 'serve']
runpy.run_module('taskconsole', run_name='__main__')
'''
    def popen(command, *args, **kwargs):
        if command == [sys.executable, '-m', 'taskconsole', 'serve']:
            command = [sys.executable, '-c', bootstrap]
        return original(command, *args, **kwargs)
    monkeypatch.setattr(subprocess, 'Popen', popen)
    yield from base_isolated_url.__wrapped__(tmp_path)


def test_browser_g1_visual_mappings_have_independent_single_invocation_witnesses(engine_url, tmp_path):
    py_marker = tmp_path / 'python-invocations.txt'
    js_marker = tmp_path / 'javascript-invocations.txt'
    suffix = '\n_original_main=main\n' + counted_source(py_marker, '_original_main(inputs)')
    browser(engine_url, HELPERS+'const suffix='+json.dumps(suffix)+';const jsMarker='+json.dumps(str(js_marker))+';'+r"""
 const {id,...template}=(await request('/api/workflow-templates'))[0];
 const sql=template.nodes.find(n=>n.id==='orders'),py=template.nodes.find(n=>n.id==='summary'),js=template.nodes.find(n=>n.id==='report');
 sql.source='WITH counted AS MATERIALIZED (SELECT acceptance_invocation() AS ok) SELECT order_id,amount,region FROM counted, orders WHERE counted.ok=1 ORDER BY order_id';py.inputs={};js.inputs={};template.edges=[];py.source+=suffix;js.source+='\nrequire("fs").appendFileSync('+JSON.stringify(jsMarker)+',"invoked\\n");';
 const w=await seed(template);await open(w);await mapping('summary','orders','node',null,{source:'orders',path:'rows',type:'array'});await mapping('report','summary','node',null,{source:'summary',path:'summary',type:'object'});const saved=await save(w);
 assert.deepEqual(saved.edges.map(e=>[e.source,e.target]),[['orders','summary'],['summary','report']]);assert.equal(saved.nodes.find(n=>n.id==='summary').inputs.orders.node_id,'orders');assert.equal(saved.nodes.find(n=>n.id==='report').inputs.summary.node_id,'summary');
 const run=await testRun(w);assert.equal(run.status,'succeeded',run.error);const rows=[{order_id:'A001',amount:'10.50',region:'华东'},{order_id:'A002',amount:'20.25',region:null},{order_id:'A003',amount:'0.00',region:'西部'}];assert.deepEqual(run.nodes.orders.output.data.rows,rows);assert.deepEqual(run.nodes.summary.inputs,{orders:rows});assert.deepEqual(run.nodes.summary.output.data,{summary:{count:3,total:'30.75'}});assert.deepEqual(run.nodes.report.inputs,{summary:{count:3,total:'30.75'}});assert.ok(Object.values(run.nodes).every(n=>n.status==='succeeded'&&n.attempts.length===1));await page.goto(origin+'/workflow-runs/'+run.id);await page.locator('.wf-run-summary').getByText('Succeeded',{exact:true}).waitFor();
 """)
    for marker in (tmp_path/'sql-invocations.txt', py_marker, js_marker):
        assert count(marker) == ['invoked']
    runs=durable_runs(tmp_path)
    assert len(runs)==1
    assert next(n for n in runs[0]['snapshot']['nodes'] if n['id']=='summary')['inputs']['orders']['node_id']=='orders'


def test_browser_g1_locale_preserves_added_node_edge_upstream_draft_and_undo(isolated_url):
    browser(isolated_url,HELPERS+r"""
 const {id,...template}=(await request('/api/workflow-templates'))[0];const w=await seed(template);await open(w);await save(w);const baseline=await request('/api/workflows/'+w.id);const writes=[],reads=[];page.on('request',r=>{if(r.url().includes('/api/workflows/'+w.id)){if(['POST','PUT'].includes(r.method()))writes.push(r.url());if(r.method()==='GET')reads.push(r.url());}});
 await page.getByRole('button',{name:'Library',exact:true}).click();await page.locator('.wf-library-item').filter({has:page.locator('[data-wf="python"]')}).click();const ids=await page.locator('article[data-node-id]').evaluateAll(xs=>xs.map(x=>x.dataset.nodeId));const added=ids.find(id=>!baseline.nodes.some(n=>n.id===id));assert.ok(added);await mapping(added,'rows','node',null,{source:'orders',path:'rows'});await selectNode(added);await page.getByLabel('Name',{exact:true}).last().fill('新增 — Unicode 中文');const code='def main(inputs):\n    return {"中文": inputs, "literal": "001.50"}\n';await page.getByLabel('Source code',{exact:true}).fill(code);
 const graphDOM=()=>page.locator('article[data-node-id]').evaluateAll(xs=>xs.map(x=>({id:x.dataset.nodeId,style:x.getAttribute('style'),selected:x.classList.contains('selected')})));const before=await graphDOM();const edges=await page.locator('.wf-edge-hit').evaluateAll(xs=>xs.map(x=>({label:x.getAttribute('aria-label'),path:x.getAttribute('d')})));const undo=await page.getByRole('button',{name:'Undo',exact:true}).isDisabled();
 await page.locator('[data-locale="zh-CN"]').click();await page.locator('[data-locale="en"]').click();assert.equal(await page.getByLabel('Source code',{exact:true}).inputValue(),code);assert.deepEqual(await graphDOM(),before);assert.deepEqual(await page.locator('.wf-edge-hit').evaluateAll(xs=>xs.map(x=>({label:x.getAttribute('aria-label'),path:x.getAttribute('d')}))),edges);assert.equal(await page.getByRole('button',{name:'Undo',exact:true}).isDisabled(),undo);
 await page.getByRole('button',{name:'Inputs',exact:true}).click();await page.getByRole('button',{name:'Add input mapping',exact:true}).click();await page.getByLabel('Target field',{exact:true}).fill('whole_rows');await page.getByLabel('Source',{exact:true}).selectOption('node');await page.getByLabel('Upstream output',{exact:true}).selectOption('orders');await page.getByLabel('Output field',{exact:true}).selectOption('["rows"]');await page.locator('[data-locale="zh-CN"]').click();await page.locator('[data-locale="en"]').click();assert.equal(await page.getByLabel('Target field',{exact:true}).inputValue(),'whole_rows');assert.equal(await page.getByLabel('Upstream output',{exact:true}).inputValue(),'orders');assert.equal(await page.getByLabel('Output field',{exact:true}).inputValue(),'["rows"]');assert.ok((await page.locator('.wf-save-state').textContent()).includes('Unsaved'));assert.deepEqual(writes,[]);assert.deepEqual(reads,[]);
 await page.getByRole('button',{name:'Map input',exact:true}).click();const saved=await save(w);const n=saved.nodes.find(n=>n.id===added);assert.equal(n.name,'新增 — Unicode 中文');assert.equal(n.source,code);assert.deepEqual(n.inputs,{rows:{source:'node',node_id:'orders',path:['rows']},whole_rows:{source:'node',node_id:'orders',path:['rows']}});assert.ok(saved.edges.some(e=>e.source==='orders'&&e.target===added));assert.deepEqual(saved.nodes.map(n=>({id:n.id,x:n.position.x,y:n.position.y})),before.map(n=>({id:n.id,x:parseFloat(n.style.match(/left:\s*([\d.-]+)/)[1]),y:parseFloat(n.style.match(/top:\s*([\d.-]+)/)[1])})));
 await page.getByRole('button',{name:'Undo',exact:true}).click();let undoSaved=await save(w);assert.equal(Object.hasOwn(undoSaved.nodes.find(n=>n.id===added).inputs,'whole_rows'),false);assert.ok(undoSaved.nodes.find(n=>n.id===added).inputs.rows);await page.getByRole('button',{name:'Redo',exact:true}).click();const redone=await save(w);assert.deepEqual(redone.nodes,saved.nodes);assert.deepEqual(redone.edges,saved.edges);await open(w);await selectNode(added);assert.equal(await page.getByLabel('Source code',{exact:true}).inputValue(),code);assert.deepEqual(await request('/api/workflow-runs'),[]);assert.equal(redone.published_version_id,null);
 """)


@pytest.mark.parametrize('join', ['all', 'any'])
def test_browser_real_diamond_optional_data_never_tolerates_required_failure(engine_url, tmp_path, join):
    marker=tmp_path/'join-counter.txt'
    source=counted_source(marker)
    browser(engine_url,HELPERS+'const joinPolicy='+json.dumps(join)+';const joinSource='+json.dumps(source)+';'+r"""
 for(const [variant,bState,cState,tolerate,expectedNode,expectedRun] of [['skipped','skipped','succeeded',false,'succeeded','succeeded'],['failed','failed','succeeded',false,'not_run','failed'],['required-failed','succeeded','failed',false,'not_run','failed'],['explicit-tolerance','failed','succeeded',true,'succeeded','partial']]){
  const branch=(id,state)=>node(id,{source:state==='failed'?'def main(inputs): raise RuntimeError("branch-'+id+'-failed")':'def main(inputs): return {"result":"'+id+'-value"}',outputs:{type:'object',properties:{result:{type:'string'}}}});
  const w=await seed({name:'Diamond '+joinPolicy+' '+variant,nodes:[node('a',{source:'def main(inputs): return {"go":True}',outputs:{type:'object',properties:{go:{type:'boolean'}}}}),branch('b',bState),branch('c',cState),node('d',{source:joinSource,config:{join:joinPolicy},inputs:{from_b:{source:'node',node_id:'b',path:['result'],optional:true,default:'explicit-fallback'},from_c:{source:'node',node_id:'c',path:['result'],optional:false}}})],edges:[{source:'a',target:'b',condition:{path:['go'],operator:'eq',value:bState!=='skipped'}},{source:'a',target:'c'},{source:'b',target:'d',required:!tolerate},{source:'c',target:'d',required:true}]});await open(w);const run=await testRun(w);assert.equal(run.status,expectedRun,JSON.stringify(run));await page.locator('.wf-drawer-head').getByText(expectedRun==='succeeded'?'Succeeded':expectedRun==='partial'?'Partial':'Failed',{exact:true}).waitFor();assert.equal(run.nodes.b.status,bState);assert.equal(run.nodes.c.status,cState);assert.equal(run.nodes.d.status,expectedNode);assert.ok(Object.values(run.nodes).every(n=>!['queued','running','cancelling'].includes(n.status)));
  if(expectedNode==='succeeded'){assert.deepEqual(run.nodes.d.inputs,{from_b:'explicit-fallback',from_c:'c-value'});assert.deepEqual(run.nodes.d.output.data,run.nodes.d.inputs);assert.equal(run.nodes.d.attempts.length,1);assert.equal(run.nodes.d.input_provenance.from_b.default_used,true);await selectNode('d');await page.getByRole('button',{name:'Inputs',exact:true}).click();await page.getByText('Sample used explicit fallback',{exact:true}).waitFor();assert.ok((await page.locator('.wf-binding').filter({has:page.getByText('from_b',{exact:true})}).textContent()).includes('explicit-fallback'));}else{assert.equal(run.nodes.d.attempts.length,0);assert.equal(run.nodes.d.output,undefined);}
  await page.goto(origin+'/workflow-runs/'+run.id);await page.locator('.wf-run-summary').getByText(expectedRun==='succeeded'?'Succeeded':expectedRun==='partial'?'Partial':'Failed',{exact:true}).waitFor();const d=page.locator('.wf-node-detail').filter({has:page.locator('summary strong').getByText('d',{exact:true})});assert.ok((await d.textContent()).includes(expectedNode==='succeeded'?'Succeeded':'Not run'));
 }
 """)
    assert count(marker)==['invoked']*2
    runs=durable_runs(tmp_path)
    assert len(runs)==4
    assert all(next(n for n in r['snapshot']['nodes'] if n['id']=='d')['config']['join']==join for r in runs)


def test_browser_declared_fields_whole_arrays_stale_and_expired_samples_remain_distinct(engine_url, tmp_path):
    expire = '''
import sys
from datetime import datetime, timezone, timedelta
from taskconsole.store import Store
store=Store(sys.argv[1], 'sqlite:///'+sys.argv[1]+'/browser.sqlite')
with store.transaction() as tx:
    run=tx.get('workflow_run',sys.argv[2])
    run['finished_at']=(datetime.now(timezone.utc)-timedelta(days=40)).isoformat()
    tx.put('workflow_run',run)
'''
    browser(engine_url,HELPERS+'const python='+json.dumps(sys.executable)+';const state='+json.dumps(str(tmp_path))+';const expire='+json.dumps(expire)+';'+r"""
 const schema={type:'object',properties:{summary:{type:'object',properties:{count:{type:'integer'}}},rows:{type:'array',items:{type:'object',properties:{value:{type:'integer'}}}}}};const w=await seed({nodes:[node('source',{source:'def main(inputs): return {"summary":{"count":3},"rows":[{"value":1},{"value":2},{"value":3}]}',outputs:schema}),node('consumer')],edges:[]});await open(w);await selectNode('source');await page.getByRole('button',{name:'Outputs',exact:true}).click();await page.getByText('No run sample',{exact:true}).waitFor();
 await mapping('consumer','all_rows','node',null,{source:'source',path:'rows',type:'array'});const before=await save(w);assert.deepEqual(before.nodes.find(n=>n.id==='consumer').inputs.all_rows,{source:'node',node_id:'source',path:['rows'],type:'array'});const prior=await testRun(w);assert.equal(prior.status,'succeeded');await page.locator('.wf-drawer-head').getByText('Succeeded',{exact:true}).waitFor();assert.equal(prior.nodes.source.output.data.summary.count,3);assert.deepEqual(prior.nodes.consumer.inputs.all_rows,[{value:1},{value:2},{value:3}]);
 await sourceCode('source','def main(inputs): return {"renamed":{"count":4},"rows":[{"value":4}]}');await page.getByRole('button',{name:'Outputs',exact:true}).click();const newSchema={type:'object',properties:{renamed:{type:'object',properties:{count:{type:'integer'}}},rows:schema.properties.rows}};await page.getByLabel('Output contract (JSON Schema)',{exact:true}).fill(JSON.stringify(newSchema));await page.getByRole('button',{name:'Configuration',exact:true}).first().click();await page.getByRole('button',{name:'Outputs',exact:true}).click();await page.getByText('Stale sample — draft changed',{exact:true}).waitFor();assert.ok((await page.locator('.wf-inspector').textContent()).includes(prior.id));
 await selectNode('consumer');await page.getByRole('button',{name:'Inputs',exact:true}).click();await page.getByRole('button',{name:'Add input mapping',exact:true}).click();await page.getByLabel('Target field',{exact:true}).fill('count');await page.getByLabel('Source',{exact:true}).selectOption('node');await page.getByLabel('Upstream output',{exact:true}).selectOption('source');await page.getByLabel('Output field',{exact:true}).selectOption('["renamed","count"]');await page.getByRole('button',{name:'Map input',exact:true}).click();const current=await testRun(w);assert.equal(current.status,'succeeded');assert.deepEqual(current.nodes.consumer.inputs,{all_rows:[{value:4}],count:4});assert.equal(current.nodes.consumer.attempts.length,1);assert.deepEqual((await request('/api/workflows/'+w.id)).nodes.find(n=>n.id==='consumer').inputs.count,{source:'node',node_id:'source',path:['renamed','count']});
 const other=await seed({nodes:[node('source',{source:'def main(inputs): return {"renamed":{"count":999},"rows":["unrelated-must-not-appear"]}'})],edges:[]});await open(other);const unrelated=await testRun(other);assert.equal(unrelated.status,'succeeded');
 (await import('node:child_process')).execFileSync(python,['-c',expire,state,prior.id]);const cleaned=await request('/api/workflow-maintenance/cleanup','POST',{});const old=await request('/api/workflow-runs/'+prior.id);assert.equal(old.data_expired,true);assert.equal(old.nodes.source.output,undefined);assert.equal((await request('/api/workflow-runs/'+current.id)).data_expired,undefined);await page.goto(origin+'/workflow-runs/'+prior.id);await page.getByText('Sample data expired under the retention policy',{exact:true}).waitFor();assert.ok((await page.locator('.wf-run-page').textContent()).includes(prior.id));assert.equal(await page.getByText('Paginated data preview',{exact:true}).count(),0);await page.locator('[data-locale="zh-CN"]').click();await page.getByText('样本数据已按保留策略过期',{exact:true}).waitFor();await page.locator('[data-locale="en"]').click();assert.equal((await page.locator('.wf-run-page').textContent()).includes('unrelated-must-not-appear'),false);
 await open(w);await selectNode('consumer');await page.getByRole('button',{name:'Inputs',exact:true}).click();await page.locator('.wf-binding').filter({has:page.getByText('count',{exact:true})}).getByRole('button',{name:'Configuration',exact:true}).click();assert.equal(await page.getByLabel('Output field',{exact:true}).inputValue(),'["renamed","count"]');assert.equal((await page.locator('.wf-inspector').textContent()).includes('999'),false);assert.equal((await page.locator('.wf-inspector').textContent()).includes('unrelated-must-not-appear'),false);
 """)
    assert len(durable_runs(tmp_path))==3
