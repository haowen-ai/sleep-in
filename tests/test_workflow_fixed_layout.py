"""Fixed downward authoring: old positions cannot override dependency layout."""
import unittest
import test_workflow_frontend as frontend

class FixedLayoutTests(unittest.TestCase):
    run_js = frontend.WorkflowFrontendTests.run_js
    def test_existing_graph_reflows_downward_without_changing_data(self):
        self.run_js("""
        const original={nodes:[{id:'a',position:{x:900,y:800},inputs:{}},{id:'b',position:{x:0,y:0},inputs:{rows:{source:'node',node_id:'a',path:'rows'}}},{id:'c',inputs:{}},{id:'d',inputs:{}}],edges:[{source:'a',target:'b'},{source:'a',target:'c'},{source:'b',target:'d'}]};
        const g=new m.GraphModel(original);
        for(const edge of g.value.edges)assert.ok(g.node(edge.target).position.y>g.node(edge.source).position.y+116);
        assert.equal(g.node('b').position.y,g.node('c').position.y);assert.ok(Math.abs(g.node('b').position.x-g.node('c').position.x)>=320);
        assert.equal(g.node('b').position.x,g.node('d').position.x);
        assert.deepEqual(g.node('b').inputs,original.nodes[1].inputs);assert.deepEqual(g.value.edges,original.edges);assert.equal(original.nodes[0].position.x,900);
        const positions=g.value.nodes.map(n=>n.position);g.move('a',{x:-900,y:1000});assert.deepEqual(g.value.nodes.map(n=>n.position),positions);
        """)

    def test_merges_and_history_always_reflow_without_new_dependencies(self):
        self.run_js("""
        const g=new m.GraphModel({nodes:['a','b','c','d','e'].map(id=>({id,inputs:{}})),edges:[]});
        for(const [a,b] of [['a','b'],['a','c'],['b','d'],['c','d'],['d','e']])g.connect(a,b);
        const check=()=>{for(const e of g.value.edges)assert.ok(g.node(e.target).position.y>g.node(e.source).position.y);for(let i=0;i<g.value.nodes.length;i++)for(const b of g.value.nodes.slice(i+1)){const a=g.value.nodes[i];assert.ok(Math.abs(a.position.y-b.position.y)>=200||Math.abs(a.position.x-b.position.x)>=320);}};
        check();g.undo();check();g.redo();check();const edges=JSON.stringify(g.value.edges);g.layout('horizontal');check();assert.equal(JSON.stringify(g.value.edges),edges);assert.deepEqual(g.node('e').inputs,{});
        """)

    def test_editor_has_only_vertical_ports_and_no_node_drag_or_arrow_move(self):
        self.run_js("""
        const {setup,button}=await import('./tests/workflow_ui_harness.mjs');const ctx=setup(),calls=[];
        ctx.request=async(path,options)=>{calls.push({path,options});if(path==='/api/workflows/demo')return {id:'demo',name:'Fixed',nodes:[{id:'n',name:'Calculate',kind:'python',inputs:{},config:{},position:{x:900,y:999}}],edges:[]};return [];};
        const ui=await import('./taskconsole/static/workflows.js');ui.initWorkflowUI(ctx);await ui.renderWorkflowRoute('/workflows/demo');
        let card=ctx.root.querySelectorAll('article').find(n=>n.dataset.nodeId==='n');
        assert.equal(card.querySelectorAll('.wf-port').length,2);assert.equal(ctx.root.querySelectorAll('select').some(s=>s.getAttribute('aria-label')==='Auto layout'),false);
        const before=card.getAttribute('style');await card.fire('pointerdown',{button:0,clientX:10,clientY:10,pointerId:1});await card.fire('pointermove',{clientX:500,clientY:500,pointerId:1});await card.fire('pointerup',{pointerId:1});assert.equal(card.getAttribute('style'),before);
        await card.fire('click');await ctx.root.querySelector('.wf-canvas').fire('keydown',{key:'ArrowDown'});
        await button(ctx.root,'Save draft').fire('click');const saved=JSON.parse(calls.find(c=>c.options?.method==='PUT').options.body);assert.equal(saved.nodes[0].position.y,100);
        assert.equal(ctx.root.querySelectorAll('.wf-library-item').some(n=>n.listeners.pointerdown?.length),false);
        """)
