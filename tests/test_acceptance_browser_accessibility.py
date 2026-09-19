"""Measured graph layouts and keyboard-only authoring in an isolated browser.

Screenshots are test-owned evidence for separate visual review. These tests do
not claim screen-reader or other assistive-technology verification.
"""
import json
import os
from pathlib import Path

import pytest

from test_acceptance_browser import browser, engine_url, isolated_url, pytestmark
from test_acceptance_browser_mapping import HELPERS, count, counted_source, durable_runs


def evidence_directory(tmp_path):
    path=Path(os.environ.get('SLEEP_IN_BROWSER_EVIDENCE_DIR',str(tmp_path/'browser-evidence')))
    path.mkdir(parents=True,exist_ok=True)
    return path


@pytest.mark.parametrize('size',[10,25,50])
def test_browser_branched_graph_layout_zoom_fit_and_outline_preserve_all_data(isolated_url,tmp_path,size):
    images=evidence_directory(tmp_path)
    result=browser(isolated_url,HELPERS+'const size='+str(size)+';const imageDir='+json.dumps(str(images))+';'+r"""
 const nodes=Array.from({length:size},(_,i)=>node('n'+i,{name:'Node '+String(i).padStart(2,'0'),source:'def main(inputs): return inputs',position:{x:-i*7,y:i%3},inputs:i?{upstream:{source:'node',node_id:'n'+Math.floor((i-1)/2),path:[]}}:{constant:{source:'constant',value:{index:0,literal:'001.50'}}}}));const edges=nodes.slice(1).map((n,i)=>({source:'n'+Math.floor(i/2),target:n.id}));const w=await seed({nodes,edges});const start=performance.now();await open(w);await page.locator('article[data-node-id]').last().waitFor();const rendered=performance.now()-start;assert.equal(await page.locator('article[data-node-id]').count(),size);assert.equal(await page.locator('.wf-edge-hit').count(),size-1);
 const normalized=await save(w);const logic=g=>g.nodes.map(({position,...n})=>n);assert.deepEqual(logic(normalized),logic(w));assert.deepEqual(normalized.edges,w.edges);const interaction=performance.now();for(let i=0;i<20;i++)await page.getByRole('button',{name:'Zoom in',exact:true}).click();const upper=await page.locator('#wf-zoom').textContent();await page.getByRole('button',{name:'Zoom in',exact:true}).click();assert.equal(await page.locator('#wf-zoom').textContent(),upper);for(let i=0;i<35;i++)await page.getByRole('button',{name:'Zoom out',exact:true}).click();const lower=await page.locator('#wf-zoom').textContent();await page.getByRole('button',{name:'Zoom out',exact:true}).click();assert.equal(await page.locator('#wf-zoom').textContent(),lower);await page.getByRole('button',{name:'Fit canvas',exact:true}).click();const interactionMs=performance.now()-interaction;
 await page.screenshot({path:imageDir+'/graph-'+size+'-fit.png',fullPage:true});const viewport=await page.locator('.wf-canvas').boundingBox();const boxes=await page.locator('article[data-node-id]').evaluateAll(xs=>xs.map(x=>{const r=x.getBoundingClientRect();return{id:x.dataset.nodeId,x:r.x,y:r.y,right:r.right,bottom:r.bottom}}));for(const b of boxes){assert.ok(b.x>=viewport.x-1&&b.right<=viewport.x+viewport.width+1&&b.y>=viewport.y-1&&b.bottom<=viewport.y+viewport.height+1,'Fit clipped '+b.id+': '+JSON.stringify({b,viewport}));}assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>document.documentElement.clientWidth+1),false);const fittedScale=parseFloat(await page.locator('#wf-zoom').textContent());await page.getByRole('button',{name:'Zoom out',exact:true}).click();assert.ok(parseFloat(await page.locator('#wf-zoom').textContent())<=fittedScale,'Zoom out after fit must not zoom in');await page.getByRole('button',{name:'Fit canvas',exact:true}).click();
 await page.getByRole('button',{name:'Node list',exact:true}).click();const far=page.locator('.wf-outline-row').filter({has:page.getByText('Node '+String(size-1).padStart(2,'0'),{exact:true})});await far.locator('.wf-library-item').click();assert.ok((await page.locator('article[data-node-id="n'+(size-1)+'"]').getAttribute('class')).includes('selected'));await page.getByLabel('Source code',{exact:true}).waitFor();await page.screenshot({path:imageDir+'/graph-'+size+'-selected.png',fullPage:true});await page.getByRole('button',{name:'Fit canvas',exact:true}).click();const saved=await save(w);assert.deepEqual(saved.nodes,normalized.nodes);assert.deepEqual(saved.edges,normalized.edges);await open(w);assert.deepEqual((await request('/api/workflows/'+w.id)).nodes,normalized.nodes);assert.equal(await page.locator('article[data-node-id]').count(),size);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>document.documentElement.clientWidth+1),false);console.log(JSON.stringify({size,render_ms:rendered,interaction_ms:interactionMs,upper,lower,screenshots:[imageDir+'/graph-'+size+'-fit.png',imageDir+'/graph-'+size+'-selected.png']}));
 """)
    measurements=json.loads(result)
    (images/f'graph-{size}-measurements.json').write_text(json.dumps(measurements,indent=2)+'\n')


def test_browser_keyboard_only_sql_python_mapping_publish_and_actual_run(engine_url,tmp_path):
    marker=tmp_path/'keyboard-python-invocations.txt'
    images=evidence_directory(tmp_path)
    source='from decimal import Decimal\n'+counted_source(marker,"{'summary':{'count':len(inputs['orders']),'total':format(sum((Decimal(x['amount']) for x in inputs['orders']),Decimal('0')),'.2f')}}")
    browser(engine_url,HELPERS+'const code='+json.dumps(source)+';const imageDir='+json.dumps(str(images))+';'+r"""
 await request('/api/workflow-templates');const w=await seed({nodes:[],edges:[]});await open(w);await page.evaluate(()=>{window.pointerEvents=0;for(const event of ['pointerdown','mousedown','click'])document.addEventListener(event,e=>{if(event!=='click'||e.detail!==0)window.pointerEvents++;},true);});
 const focused=async locator=>locator.evaluate(el=>el===document.activeElement);const tabTo=async locator=>{await locator.waitFor();for(let i=0;i<250;i++){if(await focused(locator))return;await page.keyboard.press('Tab');}throw Error('Keyboard could not reach '+await locator.evaluate(el=>el.outerHTML));};const activate=async locator=>{await tabTo(locator);await page.keyboard.press('Enter');};const edit=async(locator,value)=>{await tabTo(locator);await page.keyboard.press('ControlOrMeta+A');await page.keyboard.insertText(value);};const select=async(locator,value)=>{await tabTo(locator);const label=await locator.locator('option').evaluateAll((xs,value)=>xs.find(x=>x.value===value)?.textContent,value);assert.ok(label);await page.keyboard.press(label[0]);assert.equal(await locator.inputValue(),value);await page.keyboard.press('Tab');};
 await activate(page.getByRole('button',{name:'Library',exact:true}));await activate(page.locator('.wf-library-item').filter({has:page.locator('[data-wf="sql"]')}));const sql=await page.locator('article[data-node-id]').getAttribute('data-node-id');await edit(page.getByLabel('Name',{exact:true}).last(),'Keyboard SQL');await edit(page.getByLabel('Source code',{exact:true}),'SELECT order_id,amount,region FROM orders ORDER BY order_id');assert.equal(await focused(page.getByLabel('Source code',{exact:true})),true);await page.keyboard.press('Tab');assert.equal(await focused(page.getByLabel('Source code',{exact:true})),false,'source editor traps Tab');
 await activate(page.getByRole('button',{name:'Library',exact:true}));await activate(page.locator('.wf-library-item').filter({has:page.locator('[data-wf="python"]')}));const ids=await page.locator('article[data-node-id]').evaluateAll(xs=>xs.map(x=>x.dataset.nodeId));const py=ids.find(id=>id!==sql);await edit(page.getByLabel('Name',{exact:true}).last(),'Keyboard Python');await edit(page.getByLabel('Source code',{exact:true}),code);await page.keyboard.press('Tab');await activate(page.getByRole('button',{name:'Node list',exact:true}));await activate(page.locator('.wf-outline-row').filter({has:page.getByText('Keyboard SQL',{exact:true})}).locator('.wf-library-item'));assert.ok((await page.locator('article[data-node-id="'+sql+'"]').getAttribute('class')).includes('selected'));await activate(page.locator('.wf-outline-row').filter({has:page.getByText('Keyboard Python',{exact:true})}).locator('.wf-library-item'));
 await activate(page.getByRole('button',{name:'Inputs',exact:true}));await activate(page.getByRole('button',{name:'Add input mapping',exact:true}));await edit(page.getByLabel('Target field',{exact:true}),'orders');await select(page.getByLabel('Source',{exact:true}),'node');await select(page.getByLabel('Upstream output',{exact:true}),sql);await select(page.getByLabel('Output field',{exact:true}),'["rows"]');await activate(page.getByRole('button',{name:'Map input',exact:true}));await page.screenshot({path:imageDir+'/keyboard-mapping.png',fullPage:true});
 const labelAudit=await page.locator('.wf-studio label[for]').evaluateAll(labels=>labels.map(label=>({text:label.textContent,found:!!document.getElementById(label.htmlFor)})));assert.ok(labelAudit.every(x=>x.found));const saveResponse=page.waitForResponse(r=>r.url().endsWith('/api/workflows/'+w.id)&&r.request().method()==='PUT');await activate(page.getByRole('button',{name:'Save draft',exact:true}));assert.equal((await saveResponse).status(),200);const saved=await request('/api/workflows/'+w.id);assert.deepEqual(saved.edges,[{source:sql,target:py}]);assert.deepEqual(saved.nodes.find(n=>n.id===py).inputs,{orders:{source:'node',node_id:sql,path:['rows']}});assert.equal(saved.nodes.find(n=>n.id===py).source,code);
 const pub=page.waitForResponse(r=>r.url().endsWith('/publish'));await activate(page.getByRole('button',{name:'Publish',exact:true}));const publication=await(await pub).json();assert.ok(publication.version_id,JSON.stringify(publication));const pending=page.waitForResponse(r=>r.url().endsWith('/run'));await activate(page.getByRole('button',{name:'Run published',exact:true}));const response=await pending;assert.equal(response.status(),202);const run=await waitRun(await response.json());assert.equal(run.status,'succeeded',JSON.stringify(run));assert.deepEqual(run.nodes[py].output.data.summary,{count:3,total:'30.75'});await page.locator('.wf-drawer-head').getByText('Succeeded',{exact:true}).waitFor();await activate(page.locator('.wf-run-drawer .wf-drawer-head .toolbar').getByRole('button',{name:'Run details',exact:true}));await page.locator('.wf-run-summary').getByText('Succeeded',{exact:true}).waitFor();await page.screenshot({path:imageDir+'/keyboard-run-details.png',fullPage:true});assert.equal(await page.evaluate(()=>window.pointerEvents),0,'editor journey used pointer events');
 """)
    assert count(marker)==['invoked']
    assert len(durable_runs(tmp_path))==1


def test_run_drawer_navigation_label_names_its_destination():
    from test_workflow_frontend import WorkflowFrontendTests
    WorkflowFrontendTests().run_js("""
    const {setup,button}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),destinations=[];ctx.go=path=>destinations.push(path);
    ctx.request=async(path,options)=>{if(path==='/api/workflows/w'&&!options)return {id:'w',name:'Keyboard',published_version_id:'v',nodes:[],edges:[],params:{}};if(path==='/api/workflows/w/run')return {id:'r',status:'succeeded',nodes:{},artifacts:[]};return [];};
    const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/workflows/w');await button(ctx.root,'Run published').fire('click');
    const navigation=ctx.root.querySelector('.wf-drawer-head').querySelector('.toolbar').querySelector('button');assert.equal(navigation.textContent,'Run details');await navigation.fire('click');assert.deepEqual(destinations,['/workflow-runs/r']);await ui.renderWorkflowRoute('/workflows');
    """)
