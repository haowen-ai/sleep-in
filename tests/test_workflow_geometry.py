import subprocess
import os
import shutil
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NODE = os.environ.get('NODE') or shutil.which('node')

@unittest.skipUnless(NODE, 'Node.js is required for canvas geometry tests')
class GeometryTests(unittest.TestCase):
    def test_routes_all_directions_obstacles_overlap_and_viewport(self):
        result = subprocess.run([NODE, '--input-type=module', '-e', '''
import assert from 'node:assert/strict';
import {routeEdge, nodeRect, segmentCrosses, zoomAt, toWorld} from './taskconsole/static/workflow-geometry.js';
const node=(id,x,y)=>({id,position:{x,y}});
for(const [x,y] of [[500,0],[-500,0],[0,400],[0,-400],[500,400],[-500,-400],[500,-400],[-500,400]]) {
 const a=node('a',0,0),b=node('b',x,y),r=routeEdge(a,b,[a,b]);
 assert.equal(r.blocked,false,JSON.stringify([x,y,r]));assert.ok(r.path.startsWith('M'));
 assert.ok(['top','right','bottom','left'].includes(r.sourceSide));
 for(let i=1;i<r.points.length;i++) assert.ok(r.points[i].x===r.points[i-1].x||r.points[i].y===r.points[i-1].y);
 const end=r.points.at(-1),prev=r.points.at(-2),rect=nodeRect(b);
 if(r.targetSide==='left')assert.ok(end.x===rect.x&&prev.x<end.x);
 if(r.targetSide==='right')assert.ok(end.x===rect.x+rect.width&&prev.x>end.x);
 if(r.targetSide==='top')assert.ok(end.y===rect.y&&prev.y<end.y);
 if(r.targetSide==='bottom')assert.ok(end.y===rect.y+rect.height&&prev.y>end.y);
}
const a=node('a',0,0),b=node('b',800,0),obstacle=node('o',400,-30);
for(const nodes of [[a,b,obstacle],[a,b,{...obstacle,position:{x:350,y:0}}]]) {
 const r=routeEdge(a,b,nodes);assert.equal(r.blocked,false);
 for(let i=1;i<r.points.length;i++)assert.equal(segmentCrosses(r.points[i-1],r.points[i],nodeRect(nodes[2])),false);
}
assert.equal(routeEdge(a,node('same',0,0),[a,node('same',0,0)]).blocked,true);
const camera={x:50,y:80,zoom:1};const cursor={x:300,y:200};
assert.deepEqual(toWorld(cursor,zoomAt(camera,cursor,2)),toWorld(cursor,camera));
assert.equal(routeEdge(a,b,[a,b]).path,routeEdge(a,b,[a,b]).path);
'''], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_close_cards_choose_other_sides_and_fifty_node_graph_remains_routable(self):
        result = subprocess.run([NODE,'--input-type=module','-e','''
import assert from 'node:assert/strict';import {routeEdge,nodeRect,segmentCrosses} from './taskconsole/static/workflow-geometry.js';
const a={id:'a',position:{x:0,y:0}},b={id:'b',position:{x:250,y:0}};
assert.equal(routeEdge(a,b,[a,b]).blocked,false);
for(const count of [10,25,50]){const nodes=Array.from({length:count},(_,i)=>({id:String(i),position:{x:i%8*350,y:Math.floor(i/8)*200}}));for(let i=1;i<count;i++){const route=routeEdge(nodes[i-1],nodes[i],nodes);assert.equal(route.blocked,false);for(const node of nodes.filter(n=>n.id!==String(i)&&n.id!==String(i-1)))for(let j=1;j<route.points.length;j++)assert.equal(segmentCrosses(route.points[j-1],route.points[j],nodeRect(node)),false);}}
'''],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_fixed_graph_routes_from_bottom_to_top_around_intermediate_cards(self):
        result = subprocess.run([NODE,'--input-type=module','-e','''
import assert from 'node:assert/strict';import {routeEdge,nodeRect,segmentCrosses} from './taskconsole/static/workflow-geometry.js';import {GraphModel} from './taskconsole/static/workflow-model.js';
const g=new GraphModel({nodes:['root','left','right','leaf','merge'].map(id=>({id})),edges:[{source:'root',target:'left'},{source:'root',target:'right'},{source:'left',target:'leaf'},{source:'leaf',target:'merge'},{source:'right',target:'merge'},{source:'root',target:'merge'}]});
for(const e of g.value.edges){const a=g.node(e.source),b=g.node(e.target),r=routeEdge(a,b,g.value.nodes);assert.equal(r.blocked,false);assert.equal(r.sourceSide,'bottom');assert.equal(r.targetSide,'top');assert.equal(r.points[0].x,a.position.x+120);assert.equal(r.points.at(-1).x,b.position.x+120);for(const n of g.value.nodes.filter(n=>n!==a&&n!==b))for(let i=1;i<r.points.length;i++)assert.equal(segmentCrosses(r.points[i-1],r.points[i],nodeRect(n)),false);}
'''],cwd=ROOT,text=True,capture_output=True)
        self.assertEqual(result.returncode,0,result.stderr)
