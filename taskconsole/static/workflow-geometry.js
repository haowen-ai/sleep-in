// World coordinates are independent of viewport placement and dependency direction.
export const NODE_WIDTH=240, NODE_HEIGHT=116;
export const nodeRect=n=>({x:n.position?.x||0,y:n.position?.y||0,width:NODE_WIDTH,height:NODE_HEIGHT});
export const toWorld=(point,camera)=>({x:(point.x-camera.x)/camera.zoom,y:(point.y-camera.y)/camera.zoom});
export function zoomAt(camera,point,factor){const world=toWorld(point,camera),zoom=Math.max(.2,Math.min(2.5,camera.zoom*factor));return {zoom,x:point.x-world.x*zoom,y:point.y-world.y*zoom};}
const inside=(p,r)=>p.x>r.x&&p.x<r.x+r.width&&p.y>r.y&&p.y<r.y+r.height;
export function segmentCrosses(a,b,r){if(a.x===b.x)return a.x>r.x&&a.x<r.x+r.width&&Math.max(a.y,b.y)>r.y&&Math.min(a.y,b.y)<r.y+r.height;if(a.y===b.y)return a.y>r.y&&a.y<r.y+r.height&&Math.max(a.x,b.x)>r.x&&Math.min(a.x,b.x)<r.x+r.width;return true;}
const vectors={top:[0,-1],right:[1,0],bottom:[0,1],left:[-1,0]};
export function portPoint(n,side,flow){const r=nodeRect(n),offset=0;return {x:r.x+(side==='left'?0:side==='right'?r.width:r.width/2+offset),y:r.y+(side==='top'?0:side==='bottom'?r.height:r.height/2+offset)};}
function rounded(points){let d=`M${points[0].x},${points[0].y}`;for(let i=1;i<points.length-1;i++){const a=points[i-1],b=points[i],c=points[i+1],ab=Math.hypot(b.x-a.x,b.y-a.y),bc=Math.hypot(c.x-b.x,c.y-b.y),r=Math.min(7,ab/2,bc/2);const p={x:b.x+(a.x-b.x)*r/ab,y:b.y+(a.y-b.y)*r/ab},q={x:b.x+(c.x-b.x)*r/bc,y:b.y+(c.y-b.y)*r/bc};d+=` L${p.x},${p.y} Q${b.x},${b.y} ${q.x},${q.y}`;}return d+` L${points.at(-1).x},${points.at(-1).y}`;}
function routeSides(source,target,nodes,sides){
 const a=nodeRect(source),b=nodeRect(target),dx=b.x-a.x,dy=b.y-a.y;
 if(a.x<b.x+b.width&&a.x+a.width>b.x&&a.y<b.y+b.height&&a.y+a.height>b.y)return {blocked:true,path:'',points:[]};
 const horizontal=Math.abs(dx)/NODE_WIDTH>=Math.abs(dy)/NODE_HEIGHT;
 const preferredSource=horizontal?(dx>=0?'right':'left'):(dy>=0?'bottom':'top'),preferredTarget=horizontal?(dx>=0?'left':'right'):(dy>=0?'top':'bottom');
 const [sourceSide,targetSide]=sides||[preferredSource,preferredTarget];
 const start=portPoint(source,sourceSide,'out'),end=portPoint(target,targetSide,'in'),v=vectors[sourceSide],w=vectors[targetSide];
 const s={x:start.x+v[0]*22,y:start.y+v[1]*22},t={x:end.x+w[0]*22,y:end.y+w[1]*22};
 const rects=nodes.map(n=>{const r=nodeRect(n);return {x:r.x-12,y:r.y-12,width:r.width+24,height:r.height+24};});
 const unrelated=nodes.filter(n=>n.id!==source.id&&n.id!==target.id).map(nodeRect);
 if(unrelated.some(r=>segmentCrosses(start,s,r)||segmentCrosses(t,end,r))||rects.some(r=>inside(s,r)||inside(t,r)))return {blocked:true,path:'',points:[],sourceSide,targetSide};
 const xs=[...new Set([s.x,t.x,...rects.flatMap(r=>[r.x,r.x+r.width])])].sort((a,b)=>a-b),ys=[...new Set([s.y,t.y,...rects.flatMap(r=>[r.y,r.y+r.height])])].sort((a,b)=>a-b);
 const key=(x,y)=>y*xs.length+x,point=k=>({x:xs[k%xs.length],y:ys[Math.floor(k/xs.length)]});
 const begin=key(xs.indexOf(s.x),ys.indexOf(s.y)),finish=key(xs.indexOf(t.x),ys.indexOf(t.y));
 const dist=new Map([[begin,0]]),previous=new Map(),open=new Set([begin]);let found=false;
 while(open.size){let current,score=Infinity;for(const id of open){const p=point(id),f=dist.get(id)+Math.abs(p.x-t.x)+Math.abs(p.y-t.y);if(f<score){score=f;current=id;}}if(current===finish){found=true;break;}open.delete(current);const x=current%xs.length,y=Math.floor(current/xs.length),p=point(current);for(const [nx,ny] of [[x-1,y],[x+1,y],[x,y-1],[x,y+1]]){if(nx<0||ny<0||nx>=xs.length||ny>=ys.length)continue;const id=key(nx,ny),q=point(id);if(rects.some(r=>inside(q,r)||segmentCrosses(p,q,r)))continue;const cost=dist.get(current)+Math.abs(p.x-q.x)+Math.abs(p.y-q.y);if(cost<(dist.get(id)??Infinity)){dist.set(id,cost);previous.set(id,current);open.add(id);}}}
 if(!found)return {blocked:true,path:'',points:[],sourceSide,targetSide};
 const middle=[];for(let k=finish;k!==undefined;k=previous.get(k))middle.unshift(point(k));const raw=[start,...middle,end],points=[];
 for(const p of raw){if(points.length&&p.x===points.at(-1).x&&p.y===points.at(-1).y)continue;while(points.length>1){const a=points.at(-2),b=points.at(-1);if(a.x===b.x&&b.x===p.x||a.y===b.y&&b.y===p.y)points.pop();else break;}points.push(p);}
 // Labels live in the inspector: no label can obscure a port or card.
 return {blocked:false,points,path:rounded(points),sourceSide,targetSide};
}

export function routeEdge(source,target,nodes){return routeSides(source,target,nodes,['bottom','top']);}
