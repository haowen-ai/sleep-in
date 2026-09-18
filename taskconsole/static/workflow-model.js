const clone = value => JSON.parse(JSON.stringify(value));
export const LANGUAGES = ['sql','python','javascript','shell','java','c','cpp'];
export class GraphModel {
  constructor(value={}) { this.value=clone({...value,nodes:value.nodes||[],edges:value.edges||[]}); this.past=[];this.future=[]; }
  commit(change) { const before=clone(this.value);try {change(this.value);}catch(error){this.value=before;throw error;}this.past.push(before);if(this.past.length>100)this.past.shift();this.future=[]; }
  node(id){return this.value.nodes.find(n=>n.id===id);}
  update(id,patch){this.commit(g=>Object.assign(g.nodes.find(n=>n.id===id),clone(patch)));}
  add(node){const id=node.id||crypto.randomUUID();this.commit(g=>g.nodes.push(clone({...node,id})));return id;}
  remove(id){this.commit(g=>{g.nodes=g.nodes.filter(n=>n.id!==id);g.edges=g.edges.filter(e=>e.source!==id&&e.target!==id);});}
  duplicate(id){const n=clone(this.node(id));n.id=crypto.randomUUID();n.position={x:(n.position?.x||0)+40,y:(n.position?.y||0)+40};this.commit(g=>{g.nodes.push(n);g.edges.push(...g.edges.filter(e=>e.target===id).map(e=>({...clone(e),target:n.id})));});return n.id;}
  move(id,position){this.update(id,{position});}
  unbind(id,key){this.commit(g=>{delete g.nodes.find(n=>n.id===id).inputs[key];});}
  reaches(from,to){const seen=new Set();const walk=id=>{if(id===to)return true;if(seen.has(id))return false;seen.add(id);return this.value.edges.filter(e=>e.source===id).some(e=>walk(e.target));};return walk(from);}
  canConnect(source,target){return !!this.node(source)&&!!this.node(target)&&source!==target&&!this.reaches(target,source);}
  connect(source,target){if(!this.canConnect(source,target))throw new Error('cycle');if(this.value.edges.some(e=>e.source===source&&e.target===target))return;this.commit(g=>{g.edges.push({source,target});const n=g.nodes.find(n=>n.id===target);n.config={...n.config,join:n.config?.join||'all'};});}
  disconnect(source,target){this.commit(g=>{g.edges=g.edges.filter(e=>e.source!==source||e.target!==target);});}
  bind(id,key,binding){if(binding.source==='node'&&!this.canConnect(binding.node_id,id))throw new Error('cycle');this.commit(g=>{const n=g.nodes.find(n=>n.id===id);n.inputs={...n.inputs,[key]:clone(binding)};if(binding.source==='node'&&!g.edges.some(e=>e.source===binding.node_id&&e.target===id))g.edges.push({source:binding.node_id,target:id});n.config={...n.config,join:n.config?.join||'all'};});}
  errors(){const errors=[];for(const n of this.value.nodes)for(const [field,b] of Object.entries(n.inputs||{}))if(b.source==='node'){if(!this.node(b.node_id))errors.push({node_id:n.id,field,code:'missing_source'});else if(!this.reaches(b.node_id,n.id))errors.push({node_id:n.id,field,code:'unreachable_source'});}return errors;}
  undo(){if(!this.past.length)return;this.future.push(clone(this.value));this.value=this.past.pop();}
  redo(){if(!this.future.length)return;this.past.push(clone(this.value));this.value=this.future.pop();}
  layout(){this.commit(g=>{const depth=id=>{const incoming=g.edges.filter(e=>e.target===id);return incoming.length?1+Math.max(...incoming.map(e=>depth(e.source))):0;};const rows={};for(const n of g.nodes){const d=depth(n.id);n.position={x:80+d*320,y:100+(rows[d]||0)*170};rows[d]=(rows[d]||0)+1;}});}
}
export function outputFields(node,sample){const fields=new Set(node.kind==='sql'?['rows','columns','rowCount',...(node.config?.mode==='write'?['affectedRows']:[])]:[]);const walk=(o,p='',schema=false)=>{for(const [k,v] of Object.entries(o||{})){const path=p?`${p}.${k}`:k;fields.add(path);if(schema&&v?.properties)walk(v.properties,path,true);else if(!schema&&v&&typeof v==='object'&&!Array.isArray(v))walk(v,path);}};walk(node.outputs?.properties||node.outputs||{},'',true);walk(sample);return [...fields];}

export function readPath(value,path){for(const key of Array.isArray(path)?path:String(path||'').split('.').filter(Boolean)){if(value==null||!Object.prototype.hasOwnProperty.call(value,key))return undefined;value=value[key];}return value;}
export function previewInputs(node,outputs={},params={}){return Object.fromEntries(Object.entries(node.inputs||{}).map(([key,b])=>{let value=b.source==='constant'?b.value:readPath(b.source==='parameter'?params:outputs[b.node_id],b.path);if(value===undefined){if(b.optional&&Object.prototype.hasOwnProperty.call(b,'default'))value=b.default;else throw new Error('missing: '+key);}return [key,clone(value)];}));}
export function outputOptions(node,sample){
  const options=new Map();
  const add=path=>{const key=JSON.stringify(path);if(!options.has(key))options.set(key,{path,label:path.map((p,i)=>typeof p==='number'?`[${p}]`:/^[A-Za-z_$][\w$]*$/.test(p)?`${i?'.':''}${p}`:`[${JSON.stringify(p)}]`).join('')});};
  if(node.kind==='sql')for(const key of ['rows','columns','rowCount',...(node.config?.mode==='write'?['affectedRows']:[])])add([key]);
  const schema=(properties,path=[])=>{for(const [key,value] of Object.entries(properties||{})){const p=[...path,key];add(p);if(value?.properties)schema(value.properties,p);}};
  schema(node.outputs?.properties||{});
  const walk=(value,path=[],depth=0)=>{if(depth>8||value===null||typeof value!=='object')return;for(const [key,item] of Object.entries(value).slice(0,Array.isArray(value)?20:200)){const p=[...path,Array.isArray(value)?Number(key):key];add(p);walk(item,p,depth+1);}};
  walk(sample);return [...options.values()];
}
export function addScheduledTrigger(workflow,name){const trigger={id:crypto.randomUUID(),name,kind:'scheduled',schedule:{kind:'daily',time:'09:00'},timezone:workflow.timezone||'UTC',enabled:false,params:{}};workflow.triggers=workflow.triggers||[];workflow.triggers.push(trigger);return trigger;}
export function schedulerHealth(bootstrap,path){return (/^\/(workflows|workflow-runs|connections|runtimes|templates)(\/|$)/.test(path)?bootstrap.workflow_scheduler:bootstrap.scheduler)||{status:'unavailable'};}
export function acknowledgeSave(session,saved,revision){for(const key of ['id','created_at','updated_at','published_version_id','validation_errors'])if(Object.prototype.hasOwnProperty.call(saved,key))session.graph.value[key]=clone(saved[key]);session.dirty=(session.editRevision||0)!==revision;}
