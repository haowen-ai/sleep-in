"""Flowchart edits are atomic, undoable and never silently change data bindings."""
import unittest
import test_workflow_frontend as frontend

class StructureTests(unittest.TestCase):
    run_js = frontend.WorkflowFrontendTests.run_js

    def test_rewire_preserves_mappings_conditions_and_has_one_undo(self):
        self.run_js("""
        const g=new m.GraphModel({nodes:['a','b','c'].map(id=>({id,inputs:id==='b'?{x:{source:'node',node_id:'a',path:'rows'}}:{}})),edges:[{source:'a',target:'b',required:false,condition:{path:['ok'],operator:'truthy'}}]});
        const before=JSON.stringify(g.value);g.rewire('a','b','a','c');assert.equal(g.value.edges.length,1);assert.equal(g.value.edges[0].target,'c');assert.equal(g.value.edges[0].required,false);assert.deepEqual(g.value.edges[0].condition,{path:['ok'],operator:'truthy'});assert.equal(g.node('b').inputs.x.node_id,'a');assert.equal(g.errors()[0].code,'unreachable_source');assert.deepEqual(g.node('c').inputs,{});g.undo();assert.equal(JSON.stringify(g.value),before);g.redo();assert.equal(g.value.edges[0].target,'c');
        """)

    def test_invalid_rewire_rolls_back_edges_and_history(self):
        self.run_js("""
        const g=new m.GraphModel({nodes:['a','b','c'].map(id=>({id,inputs:{}})),edges:[{source:'a',target:'b'},{source:'b',target:'c'},{source:'a',target:'c'}]});const before=JSON.stringify(g.value);
        for(const [s,t] of [['c','a'],['a','a'],['missing','b'],['b','c']]){assert.throws(()=>g.rewire('a','b',s,t),/cycle|missingNode|duplicateEdge/);assert.equal(JSON.stringify(g.value),before);assert.equal(g.past.length,0);}
        """)

    def test_insert_on_edge_and_add_branch_are_single_undo_operations(self):
        self.run_js("""
        const g=new m.GraphModel({nodes:[{id:'a',inputs:{}},{id:'b',inputs:{x:{source:'node',node_id:'a',path:'rows'}}}],edges:[{source:'a',target:'b',condition:{path:'ok',operator:'truthy'},required:false}]});const before=JSON.stringify(g.value);
        const id=g.insertOnEdge('a','b',{kind:'python',inputs:{}});assert.deepEqual(g.value.edges,[{source:'a',target:id,condition:{path:'ok',operator:'truthy'},required:false},{source:id,target:'b',required:false}]);assert.deepEqual(g.node(id).inputs,{});assert.equal(g.errors().length,0);assert.ok(g.node(id).position.y>g.node('a').position.y&&g.node('b').position.y>g.node(id).position.y);g.undo();assert.equal(JSON.stringify(g.value),before);
        const branch=g.addAfter('a',{kind:'javascript',inputs:{}});assert.ok(g.value.edges.some(e=>e.source==='a'&&e.target==='b'));assert.ok(g.value.edges.some(e=>e.source==='a'&&e.target===branch));g.undo();assert.equal(JSON.stringify(g.value),before);
        """)

    def test_replace_node_keeps_identity_links_inputs_and_can_undo(self):
        self.run_js("""
        const g=new m.GraphModel({nodes:[{id:'a',name:'Calculate',kind:'python',source:'original',config:{runtime_id:'old'},inputs:{x:{source:'constant',value:3}},outputs:{old:{}}},{id:'b',inputs:{}}],edges:[{source:'a',target:'b'}]});const before=JSON.stringify(g.value);
        g.replaceNode('a',{kind:'javascript',source:'new',config:{},outputs:{}});assert.equal(g.node('a').name,'Calculate');assert.equal(g.node('a').kind,'javascript');assert.deepEqual(g.node('a').inputs,{x:{source:'constant',value:3}});assert.deepEqual(g.value.edges,[{source:'a',target:'b'}]);g.undo();assert.equal(JSON.stringify(g.value),before);
        """)

    def test_real_outline_actions_and_edge_endpoint_editor(self):
        self.run_js("""
        const {setup,button,labelInput}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),calls=[];
        ctx.request=async(path,options)=>{calls.push({path,options});if(path==='/api/workflows/demo'&&!options)return {id:'demo',name:'Flow',nodes:['a','b','c'].map(id=>({id,name:id,kind:'python',inputs:{},config:{}})),edges:[{source:'a',target:'b'}]};return [];};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/workflows/demo');
        assert.equal(ctx.root.querySelectorAll('.wf-outline-row').length,3);const first=ctx.root.querySelector('.wf-outline-row');assert.ok(button(first,'Edit'));assert.ok(button(first,'Duplicate node'));assert.ok(button(first,'Delete node'));
        await button(first,'Duplicate node').fire('click');assert.equal(ctx.root.querySelectorAll('.wf-outline-row').length,4);
        await ctx.root.querySelector('.wf-edge-hit').fire('click');labelInput(ctx.root,'To node').value='c';await button(ctx.root,'Apply connection').fire('click');
        await button(ctx.root,'Save draft').fire('click');const saved=JSON.parse(calls.find(c=>c.options?.method==='PUT').options.body);assert.ok(saved.edges.some(e=>e.source==='a'&&e.target==='c'));assert.ok(!saved.edges.some(e=>e.source==='a'&&e.target==='b'));
        """)

    def test_real_insert_replace_and_delete_cancel_are_reversible_draft_edits(self):
        self.run_js("""
        const {setup,button,labelInput}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),calls=[];
        ctx.request=async(path,options)=>{calls.push({path,options});if(path==='/api/workflows/demo'&&!options)return {id:'demo',name:'Flow',nodes:[{id:'a',name:'First',kind:'python',inputs:{},config:{}},{id:'b',name:'Second',kind:'python',inputs:{rows:{source:'node',node_id:'a',path:'rows'}},config:{}}],edges:[{source:'a',target:'b'}]};return [];};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/workflows/demo');
        const canvas=ctx.root.querySelector('.wf-canvas');let captures=0;canvas.setPointerCapture=()=>captures++;await canvas.fire('pointerdown',{button:0,target:ctx.root.querySelector('.wf-insert-edge'),pointerId:1});assert.equal(captures,0,'insert button must not start canvas panning');await ctx.root.querySelector('.wf-insert-edge').fire('click');await button(ctx.root,'Create node').fire('click');assert.equal(ctx.root.querySelectorAll('article').length,3);
        labelInput(ctx.root,'Language').value='javascript';await button(ctx.root,'Replace type and reset code').fire('click');
        let selected=ctx.root.querySelectorAll('.wf-outline-row').find(r=>r.className.includes('selected'));await button(selected,'Delete node').fire('click');await button(ctx.root,'Cancel').fire('click');assert.equal(ctx.root.querySelectorAll('article').length,3);
        await button(ctx.root.querySelectorAll('.wf-outline-row').find(r=>r.className.includes('selected')),'Delete node').fire('click');await button(ctx.root,'Delete from draft').fire('click');assert.equal(ctx.root.querySelectorAll('article').length,2);
        const undo=ctx.root.querySelectorAll('button').find(b=>String(b.getAttribute('aria-label'))==='Undo');await undo.fire('click');assert.equal(ctx.root.querySelectorAll('article').length,3);
        await button(ctx.root,'Save draft').fire('click');const saved=JSON.parse(calls.find(c=>c.options?.method==='PUT').options.body),inserted=saved.nodes.find(n=>!['a','b'].includes(n.id));assert.equal(inserted.kind,'javascript');assert.equal(saved.nodes.find(n=>n.id==='b').inputs.rows.node_id,'a');assert.deepEqual(inserted.inputs,{});assert.equal(saved.edges.length,2);
        """)
