// Minimal DOM exercises real route builders and event callbacks without a live account.
export class Element {
 constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.dataset={};this.listeners={};this.value='';this.style={setProperty(){}};this.classList={add(){},remove(){},toggle(){}};this.clientWidth=900;this.clientHeight=600;this.scrollLeft=0;this.scrollTop=0;}
 setAttribute(k,v){this.attributes[k]=v;if(k==='value')this.value=String(v);if(k==='class')this.className=v;if(k==='type')this.type=v;if(k==='checked')this.checked=true;if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=v;}
 getAttribute(k){return this.attributes[k];}
 append(...items){for(const item of items){if(item===null||item===undefined)continue;this.children.push(item);if(item instanceof Element)item.parentElement=this;}if(this.tagName==='select'){const options=this.children.filter(c=>c instanceof Element);this.value=(options.find(c=>Object.hasOwn(c.attributes,'selected'))||options[0])?.value||'';}}
 replaceChildren(...items){this.children=[];this.append(...items);}
 prepend(...items){this.children.unshift(...items);}
 addEventListener(k,fn){(this.listeners[k]??=[]).push(fn);}
 removeEventListener(k,fn){this.listeners[k]=(this.listeners[k]||[]).filter(f=>f!==fn);}
 matches(selector){return selector.split(',').some(s=>s===this.tagName||s.startsWith('.')&&(this.className||'').split(' ').includes(s.slice(1))||s.startsWith('#')&&this.attributes.id===s.slice(1));}
 querySelectorAll(selector){const out=[];for(const c of this.children)if(c instanceof Element){if(c.matches(selector))out.push(c);out.push(...c.querySelectorAll(selector));}return out;}
 querySelector(selector){return this.querySelectorAll(selector)[0]||null;}
 closest(selector){return this.matches(selector)?this:this.parentElement?.closest(selector)||null;}
 get textContent(){return this._text??this.children.map(c=>c instanceof Element?c.textContent:String(c)).join('');}
 set textContent(v){this._text=v;this.children=[];}
 focus(){} remove(){if(this.parentElement)this.parentElement.children=this.parentElement.children.filter(c=>c!==this);}
 setCustomValidity(){} reportValidity(){return true;} getBoundingClientRect(){return {left:0,top:0,right:900,bottom:600};}
 async fire(type,event={}){for(const fn of this.listeners[type]||[])await fn({target:this,preventDefault(){},stopPropagation(){},...event});for(let i=0;i<8;i++)await new Promise(resolve=>setTimeout(resolve,0));}
}
export function setup(){globalThis.Node=Element;globalThis.document={createElement:tag=>new Element(tag),createElementNS:(_,tag)=>{const e=new Element(tag);e.namespaceURI='http://www.w3.org/2000/svg';return e;},createTextNode:t=>String(t),querySelector:()=>null,querySelectorAll:()=>[]};globalThis.location={pathname:'/workflows/new'};globalThis.history={replaceState(){}};return {root:null,state:{locale:'en',bootstrap:{timezone:'UTC'}},admin:()=>true,shell(el){this.root=el;},setDirty(){},showToast(message,error){if(error)throw new Error(String(message));},go(path){this.navigation=path;}};}
export const button=(root,text)=>root.querySelectorAll('button').find(b=>b.textContent===text);
export const labelInput=(root,text)=>{const label=root.querySelectorAll('label').find(l=>l.textContent===text);return label?.parentElement?.children.find(c=>c instanceof Element&&['input','textarea','select'].includes(c.tagName));};
