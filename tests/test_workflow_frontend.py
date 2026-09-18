"""Real graph-state tests run in Node, independent of browser rendering."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import unittest

ROOT = Path(__file__).parents[1]
NODE = os.environ.get('NODE') or shutil.which('node') or '/Users/ec/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node'

class WorkflowFrontendTests(unittest.TestCase):
    def run_js(self, body):
        self.assertTrue((ROOT / 'taskconsole/static/workflow-model.js').exists(), 'graph editor model is not implemented')
        result = subprocess.run([NODE, '--input-type=module', '-e', "import * as m from './taskconsole/static/workflow-model.js'; import assert from 'node:assert/strict'; " + body], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        return result.stdout

    def test_mapping_adds_dependency_and_rejects_cycles_without_mutation(self):
        self.run_js("const g=new m.GraphModel({nodes:[{id:'a',inputs:{}},{id:'b',inputs:{}}],edges:[]}); g.bind('b','orders',{source:'node',node_id:'a',path:'rows'}); assert.deepEqual(g.value.edges,[{source:'a',target:'b'}]); const before=JSON.stringify(g.value); assert.throws(()=>g.connect('b','a')); assert.equal(JSON.stringify(g.value),before); assert.equal(g.value.nodes[1].inputs.orders.path,'rows');")

    def test_rename_keeps_binding_delete_exposes_broken_mapping_undo_restores(self):
        self.run_js("const g=new m.GraphModel({nodes:[{id:'a',name:'Query',inputs:{}},{id:'b',inputs:{orders:{source:'node',node_id:'a',path:'rows'}}}],edges:[{source:'a',target:'b'}]}); g.update('a',{name:'New name'}); assert.equal(g.value.nodes[1].inputs.orders.node_id,'a'); g.remove('a'); assert.equal(g.errors()[0].code,'missing_source'); g.undo(); assert.equal(g.value.nodes.length,2); assert.equal(g.errors().length,0); g.redo(); assert.equal(g.value.nodes.length,1);")

    def test_duplicate_has_new_identity_and_position_and_history_does_not_alias(self):
        self.run_js("const g=new m.GraphModel({nodes:[{id:'a',kind:'python',inputs:{x:{source:'constant',value:[1,2,3]}},position:{x:10,y:20}}],edges:[]}); const id=g.duplicate('a'); assert.notEqual(id,'a'); g.update(id,{name:'copy'}); assert.equal(g.value.nodes[0].name,undefined); assert.equal(g.value.nodes[1].position.x,50); g.undo(); assert.equal(g.value.nodes[1].name,undefined);")

    def test_field_picker_uses_contract_and_samples_preserving_whole_arrays(self):
        self.run_js("assert.deepEqual(m.outputFields({kind:'sql'}),['rows','columns','rowCount']); assert.deepEqual(m.outputFields({kind:'sql',config:{mode:'write'}}),['rows','columns','rowCount','affectedRows']); assert.ok(m.outputFields({outputs:{summary:{type:'object'}}},{summary:{count:3},rows:[{x:1}]}).includes('summary.count')); assert.ok(!m.outputFields({}, {rows:[{x:1}]}).includes('rows.0.x')); const g=new m.GraphModel({nodes:[{id:'a',inputs:{}},{id:'b',inputs:{}}],edges:[]}); assert.equal(g.canConnect('a','b'),true); assert.equal(g.canConnect('a','a'),false);")

    def test_bilingual_catalog_and_source_literals_are_complete(self):
        self.run_js("const i=await import('./taskconsole/static/workflow-i18n.js'); assert.deepEqual(Object.keys(i.catalogs.en).sort(),Object.keys(i.catalogs['zh-CN']).sort()); assert.equal(i.translate('en','workflows'),'Workflows'); assert.equal(i.translate('zh-CN','workflows'),'工作流'); const fs=await import('node:fs'); const src=fs.readFileSync('./taskconsole/static/workflows.js','utf8'); for(const match of src.matchAll(/\\bt\\(['\"]([^'\"]+)['\"]\\)/g))assert.ok(i.catalogs.en[match[1]],match[1]);")

    def test_sources_parse_and_render_without_unsafe_html(self):
        for name in ['workflow-model.js','workflow-i18n.js','workflows.js']:
            path=ROOT / 'taskconsole/static' / name
            self.assertTrue(path.exists(), f'{name} missing')
            source=path.read_text()
            self.assertNotIn('innerHTML',source)
            result=subprocess.run([NODE,'--input-type=module','--check'],input=source,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)

    def test_unused_upstream_is_never_injected_and_binding_removal_keeps_order(self):
        self.run_js("const g=new m.GraphModel({nodes:[{id:'a',inputs:{}},{id:'b',inputs:{}}],edges:[{source:'a',target:'b'}]}); assert.deepEqual(m.previewInputs(g.node('b'),{a:{rows:[1,2]}},{}),{}); g.bind('b','x',{source:'constant',value:false}); assert.deepEqual(m.previewInputs(g.node('b'),{a:{rows:[1,2]}},{}),{x:false}); g.unbind('b','x'); assert.equal(g.value.edges.length,1); assert.deepEqual(g.node('b').inputs,{});")

    def test_mixed_input_types_optional_missing_and_present_null(self):
        self.run_js("const n={inputs:{orders:{source:'node',node_id:'a',path:'rows'},region:{source:'parameter',path:'region'},flag:{source:'constant',value:false},empty:{source:'node',node_id:'a',path:'missing',optional:true,default:[]},nullable:{source:'node',node_id:'a',path:'nil',optional:true,default:'fallback'}}}; assert.deepEqual(m.previewInputs(n,{a:{rows:[1,2,3],nil:null}},{region:'West'}),{orders:[1,2,3],region:'West',flag:false,empty:[],nullable:null}); assert.throws(()=>m.previewInputs({inputs:{required:{source:'node',node_id:'a',path:'absent'}}},{a:{}},{}),/missing/);")

    def test_drag_is_one_history_move_and_duplicate_preserves_incoming_only(self):
        self.run_js("const g=new m.GraphModel({nodes:[{id:'a',inputs:{}},{id:'b',inputs:{orders:{source:'node',node_id:'a',path:'rows'}},position:{x:1,y:2}},{id:'c',inputs:{}}],edges:[{source:'a',target:'b'},{source:'b',target:'c'}]}); g.move('b',{x:200,y:300}); g.undo(); assert.deepEqual(g.node('b').position,{x:1,y:2}); const id=g.duplicate('b'); assert.ok(g.value.edges.some(e=>e.source==='a'&&e.target===id)); assert.ok(!g.value.edges.some(e=>e.source===id)); assert.equal(g.errors().length,0);")

    def test_field_picker_preserves_literal_keys_and_indexes_as_path_tokens(self):
        self.run_js("const options=m.outputOptions({outputs:{type:'object',properties:{'a.b':{type:'string'},nested:{type:'object',properties:{'two words':{type:'integer'}}},rows:{type:'array',items:{type:'object',properties:{x:{type:'number'}}}}}}},{'a.b':'literal',nested:{'two words':3},rows:[{x:4}]}); assert.ok(options.some(o=>JSON.stringify(o.path)==='[\"a.b\"]')); assert.ok(options.some(o=>JSON.stringify(o.path)==='[\"nested\",\"two words\"]')); assert.ok(options.some(o=>JSON.stringify(o.path)==='[\"rows\",0,\"x\"]')); assert.equal(m.readPath({'a.b':7},['a.b']),7); assert.equal(m.readPath({rows:[{x:4}]},['rows',0,'x']),4);")

    def test_query_sql_does_not_advertise_write_only_affected_rows(self):
        self.run_js("assert.ok(!m.outputOptions({kind:'sql',config:{mode:'query'}}).some(o=>o.label==='affectedRows')); assert.ok(m.outputOptions({kind:'sql',config:{mode:'write'}}).some(o=>o.label==='affectedRows'));")

    def test_locale_updates_labels_attributes_without_replacing_editor_values(self):
        self.run_js("const ui=await import('./taskconsole/static/workflows.js');const state={locale:'zh-CN'};ui.initWorkflowUI({state});const label={dataset:{wf:'unsaved'},textContent:'Unsaved changes'};const search={dataset:{wfPlaceholder:'search'},placeholder:'Search',value:'keep search'};const name={dataset:{wfAriaLabel:'name'},value:'draft unchanged',setAttribute(k,v){this[k]=v}};globalThis.document={querySelectorAll(selector){return selector==='[data-wf]'?[label]:selector==='[data-wf-placeholder]'?[search]:selector==='[data-wf-aria-label]'?[name]:[]}};ui.workflowLocaleChanged();assert.equal(label.textContent,'有未保存的更改');assert.equal(name['aria-label'],'名称');assert.equal(name.value,'draft unchanged');assert.equal(search.value,'keep search');assert.equal(search.placeholder,'搜索语言或节点…');")

    def test_named_schedule_creation_is_disabled_independent_and_keeps_timezone(self):
        self.run_js("const w={timezone:'Asia/Shanghai',schedule:{kind:'manual'},triggers:[]};const first=m.addScheduledTrigger(w,'Morning');const second=m.addScheduledTrigger(w,'Evening');assert.notEqual(first.id,second.id);assert.equal(first.enabled,false);assert.equal(first.timezone,'Asia/Shanghai');first.schedule.time='07:30';assert.notEqual(second.schedule.time,'07:30');assert.deepEqual(w.schedule,{kind:'manual'});assert.equal(w.triggers.length,2);")

    def test_workflow_pages_use_workflow_health_and_classic_uses_original_scheduler(self):
        self.run_js("const b={scheduler:{status:'unavailable'},workflow_scheduler:{status:'ready',last_tick:'2026-09-17T12:00:00Z'}};for(const p of ['/workflows','/workflows/abc','/connections','/runtimes','/workflow-runs/xyz','/templates'])assert.equal(m.schedulerHealth(b,p).status,'ready');assert.equal(m.schedulerHealth(b,'/tasks').status,'unavailable');assert.equal(m.schedulerHealth({},'/workflows').status,'unavailable');")

    def test_save_ack_keeps_form_references_and_does_not_clean_later_edits(self):
        self.run_js("const s={graph:new m.GraphModel({nodes:[],edges:[],schedule:{kind:'weekly',weekdays:[0]},triggers:[{id:'t',schedule:{kind:'daily',time:'09:00'}}]}),dirty:true,editRevision:3};const schedule=s.graph.value.schedule,trigger=s.graph.value.triggers[0];m.acknowledgeSave(s,{id:'w',schedule:{kind:'weekly',weekdays:[0]},triggers:[{id:'t',schedule:{kind:'daily',time:'09:00'}}],published_version_id:'v1'},3);assert.equal(s.dirty,false);assert.equal(s.graph.value.schedule,schedule);assert.equal(s.graph.value.triggers[0],trigger);s.editRevision=5;s.dirty=true;m.acknowledgeSave(s,{id:'w'},4);assert.equal(s.dirty,true);assert.equal(s.graph.value.published_version_id,'v1');")

    def test_library_pointer_drop_uses_canvas_zoom_scroll_and_cancels_outside(self):
        self.run_js("""
        const {attachLibraryDrag}=await import('./taskconsole/static/workflows.js');
        const listeners={};const classes=new Set();const item={addEventListener(k,v){listeners[k]=v},setPointerCapture(){},releasePointerCapture(){},focus(){},classList:{add(k){classes.add(k)},remove(k){classes.delete(k)}}};
        const viewport={getBoundingClientRect:()=>({left:300,top:100,right:900,bottom:600}),scrollLeft:40,scrollTop:60,classList:{toggle(){},remove(){}}};const drops=[];
        const consume=attachLibraryDrag(item,viewport,()=>2,p=>drops.push(p));
        const e=(x,y)=>({pointerId:1,button:0,clientX:x,clientY:y,preventDefault(){}});
        listeners.pointerdown(e(100,200));listeners.pointermove(e(500,300));listeners.pointerup(e(500,300));
        assert.deepEqual(drops,[{x:120,y:130}]);assert.equal(consume({detail:1}),true);assert.equal(consume({detail:0}),false);
        listeners.pointerdown(e(100,200));listeners.pointerup(e(100,200));assert.equal(consume({detail:1}),false);assert.equal(drops.length,1);
        listeners.pointerdown(e(100,200));listeners.pointermove(e(200,300));listeners.pointerup(e(200,300));assert.equal(drops.length,1);
        listeners.pointerdown(e(100,200));listeners.pointermove(e(500,300));listeners.pointercancel(e(500,300));listeners.pointerup(e(500,300));assert.equal(drops.length,1);assert.equal(classes.size,0);
        """)
