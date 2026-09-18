"""Supplemental UI acceptance assertions.

These execute the production model and DOM callbacks under Node. They deliberately
make no claim to satisfy cases that require an actual browser and persisted API.
"""
import unittest
import test_workflow_frontend as frontend


class AcceptanceUITests(unittest.TestCase):
    run_js = frontend.WorkflowFrontendTests.run_js

    def test_rejected_edges_and_bindings_preserve_history_and_existing_edit(self):
        self.run_js("""
        const g=new m.GraphModel({nodes:['a','b','c'].map(id=>({id,name:id,inputs:{}})),edges:[{source:'a',target:'b'},{source:'b',target:'c'}]});
        g.update('a',{name:'edited'});const before=JSON.stringify(g.value),history=JSON.stringify(g.past);
        for(const [source,target] of [['a','a'],['c','a']]){
          assert.throws(()=>g.connect(source,target),/cycle/);
          assert.throws(()=>g.bind(target,'bad',{source:'node',node_id:source,path:'rows'}),/cycle/);
          assert.equal(JSON.stringify(g.value),before);assert.equal(JSON.stringify(g.past),history);
        }
        g.connect('a','b');assert.equal(JSON.stringify(g.past),history);
        g.undo();assert.equal(g.node('a').name,'a');g.redo();assert.equal(JSON.stringify(g.value),before);
        """)

    def test_complete_edit_history_roundtrip_and_new_edit_clears_redo(self):
        self.run_js("""
        const g=new m.GraphModel({nodes:[{id:'a',inputs:{}}],edges:[]});const states=[JSON.stringify(g.value)];
        const save=()=>states.push(JSON.stringify(g.value));
        g.add({id:'b',kind:'python',inputs:{}});save();g.connect('a','b');save();
        g.bind('b','literal.key',{source:'constant',value:{nested:[false,0,null]}});save();
        g.update('b',{name:'新名称'});save();g.remove('a');save();
        for(let i=states.length-2;i>=0;i--){g.undo();assert.equal(JSON.stringify(g.value),states[i]);}
        for(let i=1;i<states.length;i++){g.redo();assert.equal(JSON.stringify(g.value),states[i]);}
        g.undo();g.update('b',{name:'different'});const current=JSON.stringify(g.value);g.redo();assert.equal(JSON.stringify(g.value),current);assert.equal(g.future.length,0);
        """)

    def test_sql_dialect_switch_clears_incompatible_connection_in_saved_payload(self):
        self.run_js("""
        const {setup,button,labelInput}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),calls=[];
        const dialects=['sqlite','postgresql','mysql','oracle'];
        ctx.request=async(path,options)=>{calls.push({path,options});if(path==='/api/workflows/w'&&!options)return {id:'w',name:'SQL',nodes:[{id:'sql',name:'Query',kind:'sql',inputs:{},config:{dialect:'sqlite',connection_id:'sqlite-db',mode:'query'}}],edges:[]};if(path==='/api/connections')return dialects.map(dialect=>({id:dialect+'-db',name:dialect,dialect}));return [];};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/workflows/w');
        await ctx.root.querySelector('article').fire('click');
        for(const dialect of dialects){
          const control=labelInput(ctx.root,'Database dialect');control.value=dialect;await control.fire('change');
          const connection=labelInput(ctx.root,'Connection');assert.deepEqual(connection.children.map(o=>o.value),['',dialect+'-db']);assert.equal(connection.value,'');
          connection.value=dialect+'-db';await connection.fire('change');await button(ctx.root,'Save draft').fire('click');
          const node=JSON.parse(calls.filter(c=>c.options?.method==='PUT').at(-1).options.body).nodes[0];assert.equal(node.kind,'sql');assert.equal(node.config.dialect,dialect);assert.equal(node.config.connection_id,dialect+'-db');
        }
        assert.equal(calls.some(c=>/\\/(run|publish)$/.test(c.path)),false);
        """)

    def test_duplicate_deep_copy_preserves_incoming_only_through_undo_redo(self):
        self.run_js("""
        const g=new m.GraphModel({nodes:[{id:'a',inputs:{}},{id:'b',name:'Original',source:'original',inputs:{orders:{source:'node',node_id:'a',path:'rows'},constant:{source:'constant',value:{nested:[1,2]}}}},{id:'c',inputs:{}}],edges:[{source:'a',target:'b',required:false},{source:'b',target:'c'}]});
        const before=JSON.stringify(g.value),id=g.duplicate('b'),duplicated=JSON.stringify(g.value);
        g.node(id).inputs.constant.value.nested.push(3);g.node(id).name='copy';g.node(id).source='changed';
        assert.deepEqual(g.node('b').inputs.constant.value,{nested:[1,2]});assert.equal(g.node('b').name,'Original');assert.equal(g.node('b').source,'original');
        assert.deepEqual(g.value.edges.filter(e=>e.target===id),[{source:'a',target:id,required:false}]);assert.equal(g.value.edges.some(e=>e.source===id),false);
        g.undo();assert.equal(JSON.stringify(g.value),before);g.redo();assert.equal(g.node(id).source,'changed');
        assert.notEqual(duplicated,before);assert.equal(g.errors().length,0);
        """)

    def test_published_run_retry_after_lost_response_reuses_admission_key(self):
        self.run_js("""
        const {setup,button}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),calls=[],errors=[];ctx.showToast=(message,error)=>{if(error)errors.push(String(message));};
        let admissions=0;
        ctx.request=async(path,options)=>{calls.push({path,options});if(path==='/api/workflows/w'&&!options)return {id:'w',name:'Retry',published_version_id:'v1',nodes:[],edges:[],params:{marker:'original'}};if(path==='/api/workflows/w/run'){admissions++;if(admissions===1)throw new TypeError('synthetic lost response');return {id:'r',status:'succeeded',nodes:{},artifacts:[]};}return [];};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/workflows/w');
        await button(ctx.root,'Run published').fire('click');assert.ok(errors.some(e=>e.includes('lost response')));
        await button(ctx.root,'Run published').fire('click');
        const runs=calls.filter(c=>c.path==='/api/workflows/w/run').map(c=>JSON.parse(c.options.body));
        assert.equal(runs.length,2);assert.equal(runs[0].idempotency_key,runs[1].idempotency_key,'an uncertain admission must not become a new logical run');
        assert.equal(calls.some(c=>c.options?.method==='PUT'||c.path.endsWith('/publish')),false);
        await button(ctx.root,'Run published').fire('click');
        const last=JSON.parse(calls.filter(c=>c.path==='/api/workflows/w/run').at(-1).options.body);assert.notEqual(last.idempotency_key,runs[0].idempotency_key,'a confirmed admission permits a deliberate new run');
        await ui.renderWorkflowRoute('/workflows');
        """)

    def test_uncertain_admission_keeps_identity_after_authentication_rejection(self):
        self.run_js("""
        const {setup,button}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),bodies=[];ctx.showToast=()=>{};
        ctx.request=async(path,options)=>{if(path==='/api/workflows/w'&&!options)return {id:'w',name:'Auth retry',published_version_id:'v1',nodes:[],edges:[],params:{marker:7}};if(path==='/api/workflows/w/run'){bodies.push(JSON.parse(options.body));if(bodies.length===1)throw new TypeError('lost response');if(bodies.length===2)throw {status:401,message:'Session expired'};return {id:'r',status:'succeeded',nodes:{},artifacts:[]};}return [];};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/workflows/w');
        for(let i=0;i<3;i++)await button(ctx.root,'Run published').fire('click');
        assert.equal(bodies.length,3);assert.deepEqual(bodies[1],bodies[0]);assert.deepEqual(bodies[2],bodies[0],'authentication failure cannot establish whether the earlier admission committed');
        await ui.renderWorkflowRoute('/workflows');
        """)

    def test_uncertain_invalid_admission_retires_before_corrected_request(self):
        self.run_js("""
        const {setup,button}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),bodies=[],resolutions=[];ctx.showToast=()=>{};
        ctx.request=async(path,options)=>{if(path==='/api/workflows/w'&&!options)return {id:'w',name:'Repair',published_version_id:'v1',nodes:[],edges:[],params:{n:'wrong'}};if(path.endsWith('/admission-resolution')){resolutions.push(JSON.parse(options.body));return {status:'retired',idempotency_key:resolutions.at(-1).idempotency_key};}if(path==='/api/workflows/w/run'){bodies.push(JSON.parse(options.body));if(bodies.length===1)throw new TypeError('lost before admission');if(bodies.length===2)throw {status:400,message:'Invalid params'};return {id:'r',status:'succeeded',nodes:{},artifacts:[]};}return [];};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/workflows/w');
        for(let i=0;i<3;i++)await button(ctx.root,'Run published').fire('click');
        assert.equal(resolutions.length,1);assert.equal(resolutions[0].idempotency_key,bodies[0].idempotency_key);assert.deepEqual(bodies[1],bodies[0]);assert.notEqual(bodies[2].idempotency_key,bodies[0].idempotency_key);
        await ui.renderWorkflowRoute('/workflows');
        """)

    def test_admission_resolution_loss_retains_identity_and_admitted_result_opens_run(self):
        self.run_js("""
        const {setup,button}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),bodies=[],resolutions=[];ctx.showToast=()=>{};
        ctx.request=async(path,options)=>{if(path==='/api/workflows/w'&&!options)return {id:'w',name:'Resolve',published_version_id:'v1',nodes:[],edges:[],params:{n:1}};if(path.endsWith('/admission-resolution')){resolutions.push(JSON.parse(options.body));if(resolutions.length===1)throw new TypeError('resolution response lost');return {status:'admitted',run:{id:'recovered',status:'succeeded',nodes:{},artifacts:[]}};}if(path==='/api/workflows/w/run'){bodies.push(JSON.parse(options.body));if(bodies.length===1)throw new TypeError('run response lost');throw {status:422,message:'Changed admission prerequisites'};}return [];};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/workflows/w');
        for(let i=0;i<3;i++)await button(ctx.root,'Run published').fire('click');
        assert.equal(resolutions.length,2);assert.deepEqual(resolutions[1],resolutions[0]);assert.ok(bodies.every(v=>v.idempotency_key===bodies[0].idempotency_key));assert.ok(ctx.root.textContent.includes('recovered'));
        await ui.renderWorkflowRoute('/workflows');
        """)
