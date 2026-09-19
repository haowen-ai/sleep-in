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
        self.run_js("const g=new m.GraphModel({nodes:[{id:'a',kind:'python',inputs:{x:{source:'constant',value:[1,2,3]}},position:{x:10,y:20}}],edges:[]}); const id=g.duplicate('a'); assert.notEqual(id,'a'); g.update(id,{name:'copy'}); assert.equal(g.value.nodes[0].name,undefined); assert.equal(g.value.nodes[1].position.x-g.value.nodes[0].position.x,320); g.undo(); assert.equal(g.value.nodes[1].name,undefined);")

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

    def test_fixed_positions_and_duplicate_preserves_incoming_only(self):
        self.run_js("const g=new m.GraphModel({nodes:[{id:'a',inputs:{}},{id:'b',inputs:{orders:{source:'node',node_id:'a',path:'rows'}},position:{x:1,y:2}},{id:'c',inputs:{}}],edges:[{source:'a',target:'b'},{source:'b',target:'c'}]}); const before={...g.node('b').position};g.move('b',{x:200,y:300});assert.deepEqual(g.node('b').position,before);assert.equal(g.past.length,0); const id=g.duplicate('b'); assert.ok(g.value.edges.some(e=>e.source==='a'&&e.target===id)); assert.ok(!g.value.edges.some(e=>e.source===id)); assert.equal(g.errors().length,0);")

    def test_field_picker_preserves_literal_keys_and_indexes_as_path_tokens(self):
        self.run_js("const options=m.outputOptions({outputs:{type:'object',properties:{'a.b':{type:'string'},nested:{type:'object',properties:{'two words':{type:'integer'}}},rows:{type:'array',items:{type:'object',properties:{x:{type:'number'}}}}}}},{'a.b':'literal',nested:{'two words':3},rows:[{x:4}]}); assert.ok(options.some(o=>JSON.stringify(o.path)==='[\"a.b\"]')); assert.ok(options.some(o=>JSON.stringify(o.path)==='[\"nested\",\"two words\"]')); assert.ok(options.some(o=>JSON.stringify(o.path)==='[\"rows\",0,\"x\"]')); assert.equal(m.readPath({'a.b':7},['a.b']),7); assert.equal(m.readPath({rows:[{x:4}]},['rows',0,'x']),4);")

    def test_query_sql_does_not_advertise_write_only_affected_rows(self):
        self.run_js("assert.ok(!m.outputOptions({kind:'sql',config:{mode:'query'}}).some(o=>o.label==='affectedRows')); assert.ok(m.outputOptions({kind:'sql',config:{mode:'write'}}).some(o=>o.label==='affectedRows'));")

    def test_locale_updates_labels_attributes_without_replacing_editor_values(self):
        self.run_js("const ui=await import('./taskconsole/static/workflows.js');const state={locale:'zh-CN'};ui.initWorkflowUI({state});const label={dataset:{wf:'unsaved'},textContent:'Unsaved changes'};const search={dataset:{wfPlaceholder:'search'},placeholder:'Search',value:'keep search'};const name={dataset:{wfAriaLabel:'name'},value:'draft unchanged',setAttribute(k,v){this[k]=v}};globalThis.document={querySelectorAll(selector){return selector==='[data-wf]'?[label]:selector==='[data-wf-placeholder]'?[search]:selector==='[data-wf-aria-label]'?[name]:[]}};ui.workflowLocaleChanged();assert.equal(label.textContent,'有未保存的更改');assert.equal(name['aria-label'],'名称');assert.equal(name.value,'draft unchanged');assert.equal(search.value,'keep search');assert.equal(search.placeholder,'搜索语言或节点…');")

    def test_named_schedule_creation_is_disabled_independent_and_keeps_timezone(self):
        self.run_js("const w={timezone:'Asia/Shanghai',schedule:{kind:'manual'},triggers:[]};const first=m.addScheduledTrigger(w,'Morning');const second=m.addScheduledTrigger(w,'Evening');assert.notEqual(first.id,second.id);assert.equal(first.enabled,false);assert.equal(first.timezone,'Asia/Shanghai');first.schedule.time='07:30';assert.notEqual(second.schedule.time,'07:30');assert.deepEqual(w.schedule,{kind:'manual'});assert.equal(w.triggers.length,2);")

    def test_workflow_readiness_requires_engine_health_without_overriding_worker_or_classic(self):
        self.run_js("const b={scheduler:{status:'ready'},workflow_scheduler:{status:'ready'},workflow_engine:{status:'ready',engine_status:'degraded'}};const before=JSON.stringify(b);assert.equal(m.schedulerHealth(b,'/workflows').status,'degraded');assert.equal(m.schedulerHealth(b,'/tasks').status,'ready');assert.equal(JSON.stringify(b),before);for(const status of ['verified','available-cli']){b.workflow_engine.engine_status=status;assert.equal(m.schedulerHealth(b,'/workflows').status,'ready');}b.workflow_scheduler.status='unavailable';b.workflow_engine.engine_status='degraded';assert.equal(m.schedulerHealth(b,'/workflows').status,'unavailable');b.workflow_scheduler.status='ready';b.workflow_engine={status:'unavailable'};assert.equal(m.schedulerHealth(b,'/workflows').status,'unavailable');delete b.workflow_engine;assert.equal(m.schedulerHealth(b,'/workflows').status,'unavailable');")

    def test_workflow_pages_use_workflow_health_and_classic_uses_original_scheduler(self):
        self.run_js("const b={scheduler:{status:'unavailable'},workflow_scheduler:{status:'ready',last_tick:'2026-09-17T12:00:00Z'},workflow_engine:{status:'ready',engine_status:'available-cli'}};for(const p of ['/workflows','/workflows/abc','/connections','/runtimes','/workflow-runs/xyz','/templates'])assert.equal(m.schedulerHealth(b,p).status,'ready');assert.equal(m.schedulerHealth(b,'/tasks').status,'unavailable');assert.equal(m.schedulerHealth({},'/workflows').status,'unavailable');")

    def test_save_ack_keeps_form_references_and_does_not_clean_later_edits(self):
        self.run_js("const s={graph:new m.GraphModel({nodes:[],edges:[],schedule:{kind:'weekly',weekdays:[0]},triggers:[{id:'t',schedule:{kind:'daily',time:'09:00'}}]}),dirty:true,editRevision:3};const schedule=s.graph.value.schedule,trigger=s.graph.value.triggers[0];m.acknowledgeSave(s,{id:'w',schedule:{kind:'weekly',weekdays:[0]},triggers:[{id:'t',schedule:{kind:'daily',time:'09:00'}}],published_version_id:'v1'},3);assert.equal(s.dirty,false);assert.equal(s.graph.value.schedule,schedule);assert.equal(s.graph.value.triggers[0],trigger);s.editRevision=5;s.dirty=true;m.acknowledgeSave(s,{id:'w'},4);assert.equal(s.dirty,true);assert.equal(s.graph.value.published_version_id,'v1');")

    def test_artifact_binding_and_vertical_layout_keep_semantics_and_undo(self):
        self.run_js("const g=new m.GraphModel({nodes:[{id:'a',inputs:{}},{id:'b',inputs:{}}],edges:[]});g.bind('b','file',{source:'artifact',node_id:'a',name:'report.txt'});assert.deepEqual(g.value.edges,[{source:'a',target:'b'}]);assert.throws(()=>g.bind('a','back',{source:'artifact',node_id:'b',name:'x'}));g.layout('vertical');assert.ok(g.node('b').position.y>g.node('a').position.y);assert.equal(g.node('a').position.x,g.node('b').position.x);g.undo();assert.equal(g.value.edges.length,0);g.redo();assert.ok(g.node('b').position.y>g.node('a').position.y);g.remove('a');assert.equal(g.errors()[0].code,'missing_source');")

    def test_context_preview_does_not_invent_credentials_or_artifact_paths(self):
        self.run_js("assert.deepEqual(m.previewInputs({inputs:{run:{source:'context',path:['run_id']}}},{},{},{run_id:'synthetic'}),{run:'synthetic'});assert.throws(()=>m.previewInputs({inputs:{secret:{source:'credential',credential_id:'private'}}}));")

    def test_real_editor_node_test_uses_explicit_inputs_and_runtime_selection(self):
        self.run_js("""
        const {setup,button,labelInput}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),calls=[];
        ctx.request=async(path,options)=>{calls.push({path,options});if(path==='/api/workflows/demo')return {id:'demo',name:'Synthetic',nodes:[{id:'n',name:'Calculate',kind:'python',inputs:{},config:{},position:{x:0,y:0}}],edges:[]};if(path.endsWith('/nodes/n/test'))return {id:'test1',status:'queued',nodes:{}};if(path==='/api/runtime-profiles')return [{id:'p',name:'Locked Python',language:'python',versions:[{id:'v1',number:1,status:'ready'}]}];return [];};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/workflows/demo');
        await ctx.root.querySelectorAll('article').find(n=>n.dataset.nodeId==='n').fire('click');
        labelInput(ctx.root,'Sample input object (JSON)').value='{"rows":[]}';await button(ctx.root,'Test this node').fire('click');
        const test=calls.find(c=>c.path.endsWith('/nodes/n/test'));assert.deepEqual(JSON.parse(test.options.body),{inputs:{rows:[]}});assert.equal(calls.filter(c=>c.path.endsWith('/run')).length,0);
        await button(ctx.root,'Environment').fire('click');const select=labelInput(ctx.root,'Immutable runtime version');assert.ok(select.children.some(c=>c.value==='v1'));select.value='v1';await select.fire('change');
        await button(ctx.root,'Save draft').fire('click');assert.equal(JSON.parse(calls.filter(c=>c.path==='/api/workflows/demo'&&c.options?.method==='PUT').at(-1).options.body).nodes[0].config.runtime_version_id,'v1');
        """)

    def test_operations_test_notification_requires_displayed_target_action(self):
        self.run_js("""
        const {setup,button}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),calls=[];
        ctx.request=async(path,options)=>{calls.push({path,options});if(path==='/api/workflow-channels')return [{id:'c',name:'Synthetic channel',kind:'webhook',target:'https://example.test/…',config:{url:''}}];if(path==='/api/workflow-maintenance')return {policy:{},preview:[],paused:false};return [];};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/workflows/operations');
        assert.equal(button(ctx.root,'Send test notification'),undefined);await button(ctx.root,'Review test recipient').fire('click');assert.ok(ctx.root.textContent.includes('https://example.test/…'));assert.equal(calls.some(c=>c.path.endsWith('/test')),false);await button(ctx.root,'Send test notification').fire('click');assert.equal(calls.filter(c=>c.path==='/api/workflow-channels/c/test').length,1);
        """)

    def test_operator_editor_does_not_request_admin_credential_metadata(self):
        self.run_js("""
        const {setup}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup();ctx.admin=()=>false;
        ctx.request=async path=>{assert.equal(['/api/workflow-credentials','/api/runtime-profiles','/api/source-projects'].includes(path),false);return path==='/api/workflows/demo'?{id:'demo',name:'Public',nodes:[],edges:[]}:[];};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/workflows/demo');assert.ok(ctx.root.textContent.includes('Editor'));
        """)

    def test_run_name_preview_preserves_scalar_and_empty_parameter(self):
        self.run_js("assert.equal(m.previewRunName('{workflow} · {date} · {params.customer}',{name:'Morning',params:{customer:''}},'2026-09-17'),'Morning · 2026-09-17 · ');assert.equal(m.previewRunName('{params.count}',{params:{count:0}},'2026-09-17'),'0');assert.throws(()=>m.previewRunName('{params.rows}',{params:{rows:[]}},'2026-09-17'));assert.throws(()=>m.previewRunName('{unknown}',{},'2026-09-17')); ")

    def test_guided_template_tests_before_publish_and_enables_only_after_success(self):
        self.run_js("""
        const {setup,button,labelInput}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),calls=[];let wf;
        ctx.request=async(path,options)=>{calls.push({path,options});if(path==='/api/workflow-templates')return [{id:'sample',name:'Morning',nodes:[],edges:[],params:{region:'West'}}];if(path==='/api/workflows'&&options?.method==='POST'){wf={...JSON.parse(options.body),id:'w'};return wf;}if(path==='/api/workflows/w/run')return {id:'r',status:'queued'};if(path==='/api/workflow-runs/r')return {id:'r',status:'succeeded',nodes:{},artifacts:[]};if(path==='/api/workflows/w/publish'){wf.published_version_id='v';return {id:'v'};}if(path==='/api/workflows/w'&&!options)return wf;if(path.endsWith('/preview'))return {times:[]};return wf;};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/templates');await button(ctx.root,'Guided setup').fire('click');
        labelInput(ctx.root,'Time').value='07:00';await button(ctx.root,'Review readiness').fire('click');
        assert.equal(calls.some(c=>c.path.endsWith('/publish')),false);await button(ctx.root,'Test and enable schedule').fire('click');
        const test=calls.findIndex(c=>c.path==='/api/workflows/w/run'),publish=calls.findIndex(c=>c.path==='/api/workflows/w/publish'),enable=calls.findIndex(c=>c.path==='/api/workflows/w'&&c.options?.method==='PUT');
        assert.ok(test>=0&&publish>test&&enable>publish);assert.deepEqual(JSON.parse(calls[test].options.body).params,{region:'West'});const preview=calls.findIndex(c=>c.path.endsWith('/preview'));assert.ok(preview<enable);assert.equal(JSON.parse(calls[enable].options.body).enabled,true);assert.ok(ctx.root.textContent.includes('Your workflow is ready'));
        """)

    def test_connection_setup_uses_fields_and_driver_specific_keys(self):
        self.run_js("""
        const {setup,button,labelInput}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),calls=[];
        ctx.request=async(path,options)=>{calls.push({path,options});return [];};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/connections');await button(ctx.root,'Add connection').fire('click');
        const dialect=labelInput(ctx.root,'Database dialect');dialect.value='postgresql';await dialect.fire('change');
        labelInput(ctx.root,'Name').value='Local PG';labelInput(ctx.root,'Host').value='localhost';labelInput(ctx.root,'Database name').value='report';labelInput(ctx.root,'Username').value='reader';labelInput(ctx.root,'Password').value='synthetic';
        assert.equal(ctx.root.querySelectorAll('textarea').length,0);await ctx.root.querySelector('form').fire('submit');
        const data=JSON.parse(calls.find(c=>c.options?.method==='POST').options.body);assert.equal(data.config.dbname,'report');assert.equal(data.config.port,5432);assert.equal(data.config.user,'reader');assert.equal(data.config.password,'synthetic');assert.equal(data.config.database,undefined);assert.equal(data.write_enabled,false);
        """)

    def test_guided_failed_test_never_publishes_or_enables(self):
        self.run_js("""
        const {setup,button}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),calls=[],errors=[];ctx.showToast=(msg)=>errors.push(String(msg));
        ctx.request=async(path,options)=>{calls.push({path,options});if(path==='/api/workflow-templates')return [{id:'t',name:'Fail',nodes:[],edges:[]}];if(path==='/api/workflows')return {id:'w'};if(path==='/api/workflows/w/run')return {id:'r'};if(path==='/api/workflow-runs/r')return {id:'r',status:'failed',error:'synthetic failure'};return {};};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/templates');await button(ctx.root,'Guided setup').fire('click');await button(ctx.root,'Review readiness').fire('click');await button(ctx.root,'Test and enable schedule').fire('click');assert.equal(calls.some(c=>c.path.endsWith('/publish')),false);assert.equal(calls.some(c=>c.options?.method==='PUT'),false);assert.ok(ctx.root.textContent.includes('synthetic failure'));
        """)

    def test_connection_rotation_preserves_blank_secrets_and_picks_workflow_access(self):
        self.run_js("""
        const {setup,button,labelInput}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),calls=[];
        ctx.request=async(path,options)=>{calls.push({path,options});if(path==='/api/connections')return [{id:'db',name:'Existing',dialect:'postgresql',write_enabled:false,allowed_workflows:[]}];if(path==='/api/workflows')return [{id:'w',name:'Allowed flow'}];return {};};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/connections');await button(ctx.root,'Edit connection').fire('click');
        const tls=labelInput(ctx.root,'TLS mode');tls.value='verify-full';await tls.fire('change');const access=ctx.root.querySelectorAll('input').find(n=>n.dataset.workflowId==='w');access.checked=true;await access.fire('change');
        await ctx.root.querySelector('form').fire('submit');const update=calls.find(c=>c.path==='/api/connections/db'&&c.options?.method==='PUT');assert.ok(update);const body=JSON.parse(update.options.body);assert.equal(body.config.password,undefined);assert.equal(body.config.host,undefined);assert.equal(body.config.sslmode,'verify-full');assert.deepEqual(body.allowed_workflows,['w']);
        """)

    def test_edge_condition_uses_field_operator_value_controls(self):
        self.run_js("""
        const {setup,button,labelInput}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),calls=[];const wf={id:'w',name:'Branch',nodes:[{id:'a',name:'A',kind:'python',outputs:{type:'object',properties:{count:{type:'number'}}},position:{x:0,y:0}},{id:'b',name:'B',kind:'python',position:{x:400,y:0}}],edges:[{source:'a',target:'b'}]};ctx.request=async(path,options)=>{calls.push({path,options});return path==='/api/workflows/w'?wf:[];};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/workflows/w');await ctx.root.querySelector('.wf-edge-hit').fire('click');labelInput(ctx.root,'Output field').value='["count"]';labelInput(ctx.root,'Comparison').value='gt';labelInput(ctx.root,'Value (JSON or text)').value='2';await button(ctx.root,'Save connection').fire('click');await button(ctx.root,'Save draft').fire('click');const update=calls.find(c=>c.options?.method==='PUT');assert.deepEqual(JSON.parse(update.options.body).edges[0].condition,{path:['count'],operator:'gt',value:2});
        """)

    def test_node_duration_uses_terminal_time_and_handles_unstarted_nodes(self):
        self.run_js("assert.equal(m.runDuration({}),null);assert.equal(m.runDuration({started_at:'2026-09-17T00:00:00Z',finished_at:'2026-09-17T00:00:02.500Z'}),2.5);assert.equal(m.runDuration({started_at:'2026-09-17T00:00:00Z'},Date.parse('2026-09-17T00:00:05Z')),5);assert.equal(m.runDuration({started_at:'invalid'}),null);")

    def test_system_compiler_install_requires_explicit_button_and_shows_pending(self):
        self.run_js("""
        const {setup,button}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),calls=[];
        ctx.request=async(path,options)=>{calls.push({path,options});if(path==='/api/runtime-toolchains')return {candidates:[{id:'apple-clt',name:'Apple C/C++',system_consent:true}],installed:[]};if(path==='/api/runtime-toolchains/install')return {id:'apple-clt',status:'pending'};return [];};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/runtimes');assert.equal(calls.some(c=>c.options?.method==='POST'),false);await button(ctx.root,'Install C/C++ tools (opens macOS)').fire('click');assert.ok(ctx.root.textContent.includes('Waiting for macOS installation'));assert.ok(button(ctx.root,'Check installation'));assert.equal(calls.filter(c=>c.options?.method==='POST').length,1);
        """)
