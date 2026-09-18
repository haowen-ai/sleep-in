import {api, jsonBody, setCsrf} from './api.js';
import {normalizeLocale, translate} from './i18n.js';
import {RUN_STATUSES, canCancelExecution, endOfDay, executionIsLive, manifestPayload, manifestRows, parameterRows, paramsFromRows, timestampOf} from './model.js';

const app = document.querySelector('#app');
const toast = document.querySelector('#toast');
const state = {bootstrap:null, locale:'en', tasks:[], scripts:[], runs:null};
let controlId=0;
let dirty=false;
const pendingRuns=new Map();
const t = key => Object.assign(new String(translate(state.locale, key)), {i18nKey:key});
const el = (tag, attrs={}, ...children) => {
  const node = document.createElement(tag);
  for (const [key,value] of Object.entries(attrs)) {
    if (key === 'class') node.className = value;
    else if (key === 'text') node.textContent = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (value !== false && value != null) {
      node.setAttribute(key, value === true ? '' : String(value));
      if(value?.i18nKey)node.setAttribute(`data-i18n-${key}`,value.i18nKey);
    }
  }
  for (const child of children.flat()) if (child != null) {
    if (child instanceof Node) node.append(child);
    else if (child?.timestamp) node.append(el('span',{'data-timestamp':child.timestamp},String(child)));
    else if (child?.i18nKey && tag==='option') {node.dataset.i18n=child.i18nKey;node.textContent=String(child);}
    else if (child?.i18nKey) node.append(el('span',{'data-i18n':child.i18nKey},String(child)));
    else node.append(document.createTextNode(String(child)));
  }
  return node;
};
const clear = node => { while(node.firstChild) node.removeChild(node.firstChild); };
const showToast = (message, bad=false) => { toast.textContent=message; toast.style.background=bad?'#8d211b':''; toast.classList.add('show'); setTimeout(()=>toast.classList.remove('show'),3500); };
const fmt = value => { const stamp=timestampOf(value); return stamp ? Object.assign(new String(new Intl.DateTimeFormat(state.locale,{dateStyle:'medium',timeStyle:'short'}).format(new Date(stamp))),{timestamp:stamp}) : t('common.never'); };
const route = () => location.pathname.replace(/\/$/,'') || '/tasks';
const link = (href, text, active=false) => el('a',{href,'class':active?'active':'',onclick:e=>{e.preventDefault();go(href);}},text);
const icon = name => el('span',{'class':`icon icon-${name}`,'aria-hidden':'true'});
const buttonIcons = {'tasks.create':'plus','scripts.create':'plus','common.run':'play','common.edit':'pencil','common.refresh':'refresh-cw','common.logout':'log-out'};
const button = (text, handler, kind='', disabled=false) => el('button',{type:'button','class':`button ${kind}`,onclick:handler,disabled},buttonIcons[text?.i18nKey]?icon(buttonIcons[text.i18nKey]):null,text);
const brand = () => el('div',{'class':'brand'},el('span',{'class':'brand-mark'},icon('moon-star')),el('div',{},el('strong',{},t('brand.name')),el('small',{},t('brand.caption'))));
const systemLabel = value => {const key=`states.${String(value).toLowerCase()}`;const label=translate(state.locale,key);return label===key?String(value):t(key);};
const badge = value => el('span',{'class':`badge ${String(value).toLowerCase()}`},systemLabel(value));
const field = (label, input, hint='', full=false) => {const id=input.matches?.('input,select,textarea')?(input.id||`control-${++controlId}`):null;if(id)input.id=id;return el('div',{'class':`field${full?' full':''}`},el('label',id?{for:id}:{},label),input,hint?el('span',{'class':'hint'},hint):null);};
const input = (name,value='',type='text',required=false) => el('input',{name,type,value,required,autocomplete:type==='password'?'current-password':'off'});
function errorMessage(error){const key=`errors.${error?.code||'generic'}`;const summary=translate(state.locale,key);if(summary!==key)return state.locale==='en'&&error?.message&&error.message!==summary?`${summary} ${error.message}`:summary;return error?.message||translate(state.locale,'errors.generic');}
function handleError(error){ if(error?.status===401){state.bootstrap.user=null;renderAuth(false);} showToast(errorMessage(error),true); }
async function request(path, options){ try{return await api(path,options);}catch(error){handleError(error);throw error;} }

function setLocale(locale, persist=true){
  state.locale=normalizeLocale(locale); localStorage.setItem('taskconsole.locale',state.locale); document.documentElement.lang=state.locale; document.title=String(t('brand.name'));
  if(persist&&state.bootstrap?.user) request('/api/me',{method:'PATCH',body:jsonBody({locale:state.locale})}).catch(()=>{});
  translateDOM();
  document.querySelectorAll('[data-timestamp]').forEach(n=>n.textContent=String(fmt(n.dataset.timestamp)));
  document.querySelectorAll('[data-script-key]').forEach(n=>n.textContent=`${t(n.dataset.scriptKey)} · v${n.dataset.version}`);
  document.querySelectorAll('.locale button').forEach(button=>{const active=button.dataset.locale===state.locale;button.classList.toggle('active',active);button.setAttribute('aria-pressed',String(active));});
}
function translateDOM(){ document.querySelectorAll('[data-i18n]').forEach(n=>n.textContent=t(n.dataset.i18n));document.querySelectorAll('*').forEach(node=>{for(const attr of [...node.attributes])if(attr.name.startsWith('data-i18n-')&&attr.name!=='data-i18n-placeholder'){const target=attr.name.slice(10);node.setAttribute(target,t(attr.value));}});document.querySelectorAll('[data-i18n-placeholder]').forEach(n=>n.placeholder=t(n.dataset.i18nPlaceholder)); }
function localeSwitch(){ return el('div',{'class':'locale','aria-label':t('common.language')},...['en','zh-CN'].map(locale=>el('button',{type:'button','class':locale===state.locale?'active':'','data-locale':locale,'aria-pressed':locale===state.locale?'true':'false','aria-label':locale==='en'?t('common.english'):t('common.chinese'),onclick:()=>setLocale(locale)},locale==='en'?'EN':'中文'))); }
function sampleText(script,field){const number={'sample-1':'1','sample-2':'2','sample-3':'3'}[script?.script_id||script?.id];if(!number){if(field==='name'&&script?.version_id)return script.script_name||'';return script?.[field]||'';}return t(`samples.sample${number}${field==='name'?'Name':'Description'}`);}

function shell(content){
  document.title=String(t('brand.name'));
  document.querySelector('.skip-link').textContent=String(t('common.skip'));
  const path=route(), user=state.bootstrap.user;
  const nav=[['/tasks','nav.tasks','calendar-clock'],['/scripts','nav.scripts','file-code-2'],['/runs','nav.runs','list-checks']];
  const navItem=([href,key,glyph])=>{const item=link(href,t(key),href==='/tasks'?path.startsWith('/tasks'):path.startsWith(href));item.prepend(icon(glyph));return item;};
  const navigation=el('nav',{'class':'nav','aria-label':t('common.primaryNav')},el('div',{'class':'nav-label'},t('brand.workspace')),...nav.map(navItem));
  if(user.role==='admin')navigation.append(el('div',{'class':'nav-label nav-divider'},t('nav.admin')),...[['/admin/settings','nav.settings','settings-2'],['/admin/users','nav.users','circle-user-round'],['/admin/variables','nav.variables','code-2'],['/admin/audit','nav.audit','terminal']].map(navItem));
  const account=link('/account',el('div',{'class':'user-identity'},el('span',{'class':'avatar'},user.username.slice(0,1).toUpperCase()),el('div',{},el('strong',{},user.username),el('small',{},systemLabel(user.role)))),path==='/account');
  const side=el('aside',{'class':'sidebar',id:'sidebar'},brand(),navigation,el('div',{'class':'side-foot'},el('div',{'class':'sidebar-note'},icon('moon-star'),el('p',{},t('brand.aside'))),account,button(el('span',{},icon('log-out'),t('nav.logout')),logout,'ghost logout')));
  const section=path.startsWith('/scripts')?'nav.scripts':path.startsWith('/runs')?'nav.runs':path.startsWith('/admin')?'nav.admin':path==='/account'?'nav.account':'nav.tasks';
  const menu=button(icon('menu'),()=>side.classList.toggle('open'),'mobile-menu');menu.setAttribute('aria-label',String(t('common.primaryNav')));
  const health=el('div',{'class':'service-status'},badge(state.bootstrap.scheduler?.status||'unavailable'),el('span',{'class':'health-caption'},t('admin.scheduler')));
  const main=el('div',{'class':'workspace'},el('header',{'class':'topbar'},el('div',{'class':'breadcrumb'},menu,el('span',{},t('brand.workspace')),icon('chevron-right'),el('strong',{},t(section))),el('div',{'class':'header-actions'},health,localeSwitch())),el('main',{id:'main','class':'content',tabindex:'-1'},content),el('footer',{'class':'workspace-footer'},t('brand.footer')));
  clear(app);app.append(el('div',{'class':'shell'},side,main));app.setAttribute('aria-busy','false');
}
function pageHead(title,subtitle,action){return el('div',{'class':'page-head'},el('div',{},el('h1',{},title),el('p',{},subtitle)),action||null);}
function empty(text, action){return el('div',{'class':'empty'},el('strong',{},text),action||null);}
function table(headers, rows){const body=el('tbody');rows.forEach(cells=>body.append(el('tr',{},...cells.map(cell=>el('td',{},cell)))));return el('div',{'class':'card table-wrap'},el('table',{'class':'table'},el('thead',{},el('tr',{},...headers.map(h=>el('th',{},h)))),body));}

async function bootstrap(){
  state.locale=normalizeLocale(localStorage.getItem('taskconsole.locale'));
  try{state.bootstrap=await api('/api/bootstrap');setCsrf(state.bootstrap.csrf); if(state.bootstrap.user) state.locale=normalizeLocale(state.bootstrap.user.locale||state.locale); render();}
  catch(error){app.textContent=errorMessage(error);}
}
function render(){document.documentElement.lang=state.locale;if(!state.bootstrap?.initialized)return renderAuth(true);if(!state.bootstrap?.user)return renderAuth(false);const p=route();if(p==='/tasks')return renderTasks();if(p==='/tasks/new')return renderTaskForm();if(/^\/tasks\/[^/]+$/.test(p))return renderTaskDetail(p.split('/')[2]);if(/^\/tasks\/[^/]+\/edit$/.test(p))return renderTaskForm(p.split('/')[2]);if(p==='/scripts')return renderScripts();if(p==='/scripts/new')return renderScriptForm();if(/^\/scripts\/[^/]+$/.test(p))return renderScriptDetail(p.split('/')[2]);if(p==='/runs')return renderRuns();if(/^\/runs\/[^/]+$/.test(p))return renderRunDetail(p.split('/')[2]);if(p.startsWith('/admin/'))return renderAdmin(p.split('/')[2]);if(p==='/account')return renderAccount();history.replaceState({},'','/tasks');renderTasks();}
window.addEventListener('popstate',render);

function renderAuth(setup){
  document.title=String(t('brand.name'));
  document.querySelector('.skip-link').textContent=String(t('common.skip'));
  const form=el('form',{'class':'fields'}); const title=setup?t('auth.setup'):t('auth.signIn');
  if(setup) form.append(field(t('auth.token'),input('token','', 'password',true),'',true));
  form.append(field(t('auth.username'),input('username','','text',true),'',true),field(t('auth.password'),input('password','','password',true),'',true));
  if(setup) form.append(field(t('auth.timezone'),input('timezone',state.bootstrap?.timezone||Intl.DateTimeFormat().resolvedOptions().timeZone,'text',true),'',true));
  form.append(el('div',{'class':'field full'},localeSwitch()),el('div',{'class':'field full'},el('button',{'class':'button primary',type:'submit'},title)));
  form.addEventListener('submit',async e=>{e.preventDefault();const data=Object.fromEntries(new FormData(form));data.locale=state.locale;try{const result=await api(setup?'/api/setup':'/api/login',{method:'POST',body:jsonBody(data)});state.bootstrap.user=result.user;state.bootstrap.initialized=true;setCsrf(result.csrf);history.replaceState({},'','/tasks');render();}catch(error){handleError(error);}});
  clear(app);
  app.append(el('div',{'class':'auth'},
    el('section',{'class':'auth-art'},
      brand(),
      el('h1',{},t('auth.welcome')),
      el('p',{},t('auth.tagline')),el('div',{'class':'auth-promise'},icon('check'),t('brand.promise'))),
    el('section',{'class':'auth-panel'},
      el('div',{'class':'auth-box'},el('h2',{},title),el('p',{},setup?t('auth.setupHint'):t('auth.signInHint')),form))));
  app.setAttribute('aria-busy','false');
}
async function logout(){try{await request('/api/logout',{method:'POST'});}finally{state.bootstrap.user=null;setCsrf(null);history.replaceState({},'','/');renderAuth(false);}}

async function renderTasks(){
  const search=input('search','','search');search.placeholder=String(t('common.search'));search.dataset.i18nPlaceholder='common.search';search.setAttribute('aria-label',t('common.search'));search.setAttribute('data-i18n-aria-label','common.search');
  const status=el('select',{name:'status','aria-label':t('common.status')},el('option',{value:''},t('common.all')),el('option',{value:'active'},t('common.enabled')),el('option',{value:'disabled'},t('common.disabled')));
  const archived=el('select',{name:'archived','aria-label':t('common.archive')},el('option',{value:'current'},t('states.active')),el('option',{value:'archived'},t('states.archived')),el('option',{value:'all'},t('common.all')));
  const results=el('div',{},empty(t('common.loading')));
  const filters=el('div',{'class':'filter-bar'},el('div',{'class':'search-field'},icon('search'),search),el('div',{'class':'toolbar'},status,archived));
  const summary=el('div',{'class':'summary-grid'});
  const content=el('div',{},pageHead(t('tasks.title'),t('tasks.subtitle'),button(t('tasks.create'),()=>go('/tasks/new'),'primary')),summary,el('section',{'class':'task-panel'},filters,results));
  shell(content);
  try{
    state.tasks=await request('/api/tasks');
    const current=state.tasks.filter(task=>!task.archived),next=current.filter(task=>task.enabled&&task.next_run).map(task=>task.next_run).sort()[0];
    for(const [label,value,glyph] of [[t('tasks.total'),current.length,'calendar-clock'],[t('tasks.enabledCount'),current.filter(task=>task.enabled).length,'check'],[t('tasks.nextScheduled'),next?fmt(next):t('common.never'),'clock-3']])summary.append(el('div',{'class':'summary-item'},el('div',{},el('small',{},label),el('strong',{},value)),el('span',{'class':'summary-icon'},icon(glyph))));
    const draw=()=>{
      const query=search.value.trim().toLowerCase();
      const filtered=state.tasks.filter(task=>(!query||task.name.toLowerCase().includes(query)||task.script_name.toLowerCase().includes(query))&&(!status.value||(status.value==='active'?task.enabled:!task.enabled))&&(archived.value==='all'||Boolean(task.archived)===(archived.value==='archived')));
      const rows=filtered.map(task=>[el('div',{'class':'task-name'},el('button',{'class':'link',onclick:()=>go(`/tasks/${task.id}`)},task.name),el('div',{'class':'hint'},sampleText(task,'name')||task.script_name||'')),badge(task.archived?'archived':task.enabled?'active':'disabled'),el('div',{'class':'schedule-cell'},el('span',{},t(`tasks.${task.schedule.kind}`)),el('small',{},task.timezone)),fmt(task.next_run),fmt(task.last_run),el('div',{'class':'toolbar'},button(t('common.edit'),()=>go(`/tasks/${task.id}/edit`)),button(t('common.run'),()=>runTask(task.id),'primary'))]);
      clear(results);results.append(rows.length?table([t('common.name'),t('common.status'),t('tasks.schedule'),t('tasks.nextRun'),t('tasks.lastRun'),t('common.actions')],rows):el('div',{'class':'onboarding'},icon('calendar-clock'),empty(t('tasks.noTasks')),el('p',{},t('tasks.getStarted'))));
    };
    search.addEventListener('input',draw);status.addEventListener('change',draw);archived.addEventListener('change',draw);draw();
  }catch{}
}
async function runTask(id){
  if(pendingRuns.has(id))return pendingRuns.get(id);
  const operation=(async()=>{try{const run=await request(`/api/tasks/${id}/run`,{method:'POST',headers:{'Idempotency-Key':crypto.randomUUID()}});showToast(t('tasks.runQueued'));go(`/runs/${run.id}`,true);return run;}catch{}finally{pendingRuns.delete(id);}})();
  pendingRuns.set(id,operation);
  return operation;
}
function go(path,force=false){if(dirty&&!force&&!confirm(t('common.discard')))return;dirty=false;history.pushState({},'',path);render();}
function trackDirty(form){form.addEventListener('input',()=>{dirty=true;});form.addEventListener('change',()=>{dirty=true;});}
window.addEventListener('beforeunload',event=>{if(dirty){event.preventDefault();event.returnValue='';}});
async function renderTaskForm(id){
  let task=null;try{const [scripts,current]=await Promise.all([request('/api/scripts'),id?request(`/api/tasks/${id}`):Promise.resolve(null)]);state.scripts=scripts;task=current;}catch{return;}
  const form=el('form');const versions=state.scripts.flatMap(s=>(s.versions||[]).filter(v=>v.status==='published').map(v=>({id:v.id,label:`${sampleText(s,'name')} · v${v.number}`,scriptKey:sampleText(s,'name')?.i18nKey,number:v.number,manifest:v.manifest||[]})));
  const version=el('select',{name:'version_id',required:true},el('option',{value:''},'—'),...versions.map(v=>el('option',{value:v.id,'data-script-key':v.scriptKey,'data-version':v.number,selected:String(v.id)===String(task?.version_id)},v.label)));
  const kind=el('select',{name:'schedule_kind'},...['manual','interval','daily','weekdays','weekly','monthly','cron'].map(k=>el('option',{value:k,selected:k===(task?.schedule?.kind||'manual')},t(`tasks.${k}`))));
  const dynamic=el('div',{'class':'fields field full'});const preview=el('div',{'class':'card form-card'},el('h2',{},t('tasks.preview')),el('div',{id:'preview'},t('tasks.previewEmpty')));
  const renderSchedule=()=>{
    clear(dynamic);
    const k=kind.value;
    if(k==='interval')dynamic.append(field(t('tasks.every'),input('every',task?.schedule?.every||60,'number',true)));
    if(['daily','weekdays','weekly','monthly'].includes(k))dynamic.append(field(t('tasks.time'),input('time',task?.schedule?.time||'09:00','time',true)));
    if(k==='weekly'){
      const selected=new Set(task?.schedule?.weekdays||[0]);
      const picker=el('div',{'class':'toolbar'},...['mon','tue','wed','thu','fri','sat','sun'].map((day,index)=>el('label',{'class':'check'},el('input',{type:'checkbox',name:'weekdays',value:index,checked:selected.has(index)}),t(`tasks.${day}`))));
      dynamic.append(el('div',{'class':'field full'},el('span',{'class':'label'},t('tasks.days')),picker));
    }
    if(k==='monthly')dynamic.append(field(t('tasks.dayOfMonth'),input('day',task?.schedule?.day||1,'number',true)));
    if(k==='cron')dynamic.append(field(t('tasks.cronExpr'),input('cron',task?.schedule?.cron||'0 9 * * *','text',true)));
  };
  kind.addEventListener('change',renderSchedule);renderSchedule();
  const rows=el('div',{id:'params'});const renderParams=()=>{const selected=versions.find(v=>String(v.id)===String(version.value));clear(rows);parameterRows(selected?.manifest,task?.params||{}).forEach(row=>rows.append(kvRow(row.key,row.value,row.required,manifestRows(selected?.manifest).some(x=>x.key===row.key))));};version.addEventListener('change',renderParams);renderParams();
  form.append(el('div',{'class':'grid'},el('section',{'class':'card form-card span-8'},el('h2',{},id?t('tasks.edit'):t('tasks.create')),el('div',{'class':'fields'},field(t('common.name'),input('name',task?.name||'','text',true),'',true),field(t('tasks.scriptVersion'),version,t('tasks.immutable'),true),field(t('tasks.schedule'),kind),field(t('tasks.timezone'),input('timezone',task?.timezone||state.bootstrap.timezone,'text',true)),dynamic,field(t('tasks.timeout'),input('timeout',task?.timeout||10800,'number',true)),field(t('tasks.recipients'),el('textarea',{name:'recipients',rows:'3'},(task?.recipients||[]).join('\n')),t('tasks.recipientHelp')),el('div',{'class':'field full'},el('span',{'class':'label'},t('tasks.parameters')),rows,button(t('common.add'),()=>rows.append(kvRow()))),el('label',{'class':'check full'},el('input',{name:'enabled',type:'checkbox',checked:task?.enabled!==false}),t('common.enabled'))),el('div',{'class':'form-actions'},button(t('common.cancel'),()=>go('/tasks')),el('button',{'class':'button primary',type:'submit'},t('common.save')))),el('aside',{'class':'span-4'},preview)));
  form.addEventListener('change',()=>previewSchedule(form,preview.querySelector('#preview')));form.addEventListener('submit',e=>saveTask(e,id));trackDirty(form);shell(el('div',{},pageHead(id?t('tasks.edit'):t('tasks.create'),t('tasks.subtitle')),form));previewSchedule(form,preview.querySelector('#preview'));
}
function kvRow(key='',value='',required=false,fixed=false){const keyInput=input('param_key',key);if(fixed)keyInput.readOnly=true;const valueInput=el('textarea',{name:'param_value',rows:'2'},value);if(required)valueInput.required=true;const row=el('div',{'class':'kv','data-fixed':fixed?'true':'false','data-required':required?'true':'false'},keyInput,valueInput,fixed?el('span',{'class':'badge'},required?t('common.required'):t('common.optional')):button('×',()=>row.remove(),'danger'));return row;}
function scheduleFrom(form){const data=Object.fromEntries(new FormData(form));const s={kind:data.schedule_kind};if(data.every)s.every=Number(data.every);if(data.time)s.time=data.time;if(s.kind==='weekly')s.weekdays=[...form.querySelectorAll('[name=weekdays]:checked')].map(item=>Number(item.value));if(data.day)s.day=Number(data.day);if(data.cron)s.cron=data.cron;return s;}
async function previewSchedule(form,target){const data=Object.fromEntries(new FormData(form));try{const result=await request('/api/preview',{method:'POST',body:jsonBody({schedule:scheduleFrom(form),timezone:data.timezone})});clear(target);if(!(result.times||[]).length)target.append(el('p',{'class':'hint'},t('tasks.manualPreview')));(result.times||[]).forEach(x=>target.append(el('div',{'class':'hint'},fmt(x))));}catch{target.textContent=t('tasks.previewEmpty');}}
async function saveTask(e,id){
  e.preventDefault();const form=e.currentTarget;const d=Object.fromEntries(new FormData(form));
  let params;
  try{params=paramsFromRows([...form.querySelectorAll('.kv')].filter(row=>!(row.dataset.fixed==='true'&&row.dataset.required==='false'&&!row.querySelector('[name=param_value]').value)).map(row=>({key:row.querySelector('[name=param_key]').value,value:row.querySelector('[name=param_value]').value})));}
  catch(error){showToast(error.message,true);return;}
  const body={name:d.name,version_id:d.version_id,params,recipients:d.recipients.split('\n').map(x=>x.trim()).filter(Boolean),schedule:scheduleFrom(form),timezone:d.timezone,timeout:Number(d.timeout),enabled:form.elements.enabled.checked};
  try{await request(id?`/api/tasks/${id}`:'/api/tasks',{method:id?'PUT':'POST',body:jsonBody(body)});dirty=false;go('/tasks',true);}catch{}
}
async function renderTaskDetail(id){try{const task=await request(`/api/tasks/${id}`);const cards=el('div',{'class':'grid'},...[[t('tasks.nextRun'),fmt(task.next_run)],[t('tasks.lastRun'),fmt(task.last_run)],[t('tasks.timezone'),task.timezone],[t('tasks.revision'),task.revision]].map(([a,b])=>el('div',{'class':'card stat span-4'},el('small',{},a),el('strong',{},b))));shell(el('div',{},pageHead(task.name,sampleText(task,'name')||task.script_name,el('div',{'class':'toolbar'},button(t('tasks.history'),()=>go(`/runs?task_id=${encodeURIComponent(id)}`)),button(t('common.edit'),()=>go(`/tasks/${id}/edit`)),button(task.enabled?t('common.disabled'):t('common.enabled'),()=>taskAction(id,'toggle',{enabled:!task.enabled})),button(task.archived?t('common.restore'):t('common.archive'),()=>taskAction(id,'archive',{archived:!task.archived}),'danger'),button(t('common.run'),()=>runTask(id),'primary'))),cards,el('section',{'class':'card form-card'},el('h2',{},t('tasks.schedule')),el('pre',{'class':'logs'},JSON.stringify(task.schedule,null,2)))));}catch{}}
async function taskAction(id,action,body){if(action==='archive'&&!confirm(t('tasks.archiveConfirm')))return;try{await request(`/api/tasks/${id}/${action}`,{method:'POST',body:jsonBody(body)});renderTaskDetail(id);}catch{}}

async function renderScripts(){const admin=state.bootstrap.user.role==='admin';shell(el('div',{},pageHead(t('scripts.title'),t('scripts.subtitle'),admin?button(t('scripts.create'),()=>go('/scripts/new'),'primary'):null),empty(t('common.loading'))));try{state.scripts=await request('/api/scripts');const rows=state.scripts.map(s=>{const defaultVersion=(s.versions||[]).find(v=>v.id===s.default_version);const name=sampleText(s,'name');return [admin?el('button',{'class':'link',onclick:()=>go(`/scripts/${s.id}`)},name):name,badge(s.archived?'archived':'active'),String((s.versions||[]).length),defaultVersion?`v${defaultVersion.number}`:'—',admin?button(t('common.details'),()=>go(`/scripts/${s.id}`)):'—'];});shell(el('div',{},pageHead(t('scripts.title'),t('scripts.subtitle'),admin?button(t('scripts.create'),()=>go('/scripts/new'),'primary'):null),state.scripts.length?table([t('common.name'),t('common.status'),t('scripts.versions'),t('scripts.defaultVersion'),t('common.actions')],rows):el('div',{'class':'card'},empty(t('scripts.noScripts'),admin?button(t('scripts.create'),()=>go('/scripts/new'),'primary'):null))));}catch{}}
function manifestRow(item={}){const row=el('div',{'class':'manifest'},input('m_key',item.key||''),input('m_label',item.label||''),el('select',{name:'m_type'},el('option',{value:'string'},'string')),input('m_help',item.help||''),el('label',{'class':'check'},el('input',{name:'m_required',type:'checkbox',checked:item.required}),t('scripts.required')),input('m_default',item.default??''),button('×',()=>row.remove(),'danger'));return row;}
function renderScriptForm(script=null){if(state.bootstrap.user.role!=='admin')return go('/scripts');const form=el('form');const source=input('source_mode','paste','hidden');const manifest=el('div');const existing=manifestRows(script?.manifest);(existing.length?existing:[{}]).forEach(x=>manifest.append(manifestRow(x)));const text=el('textarea',{name:'source','class':'code',spellcheck:'false'},script?.source||'def main(params):\n    return {"message": "Hello"}\n');const file=el('input',{name:'file',type:'file',accept:'.py,.zip'});const mode=el('div',{'class':'segmented'},button(t('scripts.paste'),()=>{source.value='paste';text.closest('.field').hidden=false;file.closest('.field').hidden=true;},'active'),button(t('scripts.upload'),()=>{source.value='upload';text.closest('.field').hidden=true;file.closest('.field').hidden=false;}));file.closest;
  const fileField=field(t('scripts.file'),file,t('scripts.uploadHint'),true);
  fileField.hidden=true;
  form.append(el('section',{'class':'card form-card'},
    el('div',{'class':'fields'},
      field(t('common.name'),input('name',script?.name||'','text',true)),
      field(t('common.description'),input('description',script?.description||'')),
      el('div',{'class':'field full'},mode,source),
      field(t('scripts.source'),text,t('scripts.sourceHelp'),true),
      fileField,
      field(t('scripts.requirements'),el('textarea',{name:'requirements',rows:'4'},script?.requirements||''),'',true),
      el('div',{'class':'field full'},el('span',{'class':'label'},t('scripts.manifest')),el('span',{'class':'hint'},t('scripts.manifestHelp')),manifest,button(t('common.add'),()=>manifest.append(manifestRow())))),
    el('div',{'class':'form-actions'},button(t('common.cancel'),()=>go('/scripts')),el('button',{'class':'button primary',type:'submit'},t('common.save')))));
  form.addEventListener('submit',e=>saveScript(e,script?.id));
  trackDirty(form);
  shell(el('div',{},pageHead(script?t('scripts.edit'):t('scripts.create'),t('scripts.subtitle')),form));
}
async function saveScript(e,id){e.preventDefault();const form=e.currentTarget;const fd=new FormData(form);const rows=[...form.querySelectorAll('.manifest')];const manifest=manifestPayload(rows.map(r=>({key:r.querySelector('[name=m_key]').value,label:r.querySelector('[name=m_label]').value,type:'string',help:r.querySelector('[name=m_help]').value,required:r.querySelector('[name=m_required]').checked,default:r.querySelector('[name=m_default]').value})).filter(x=>x.key));try{if(fd.get('source_mode')==='upload'){fd.set('manifest',JSON.stringify(manifest));await request('/api/scripts/upload',{method:'POST',body:fd});}else{await request(id?`/api/scripts/${id}`:'/api/scripts',{method:id?'PATCH':'POST',body:jsonBody({name:fd.get('name'),description:fd.get('description'),source:fd.get('source'),requirements:fd.get('requirements'),manifest})});}dirty=false;go('/scripts',true);}catch{}}
async function renderScriptDetail(id){try{const s=await request(`/api/scripts/${id}`);const versions=s.versions||[];const rows=versions.map(v=>[`v${v.number}`,badge(v.status),systemLabel(v.mode||'—'),fmt(v.created_at),el('div',{'class':'toolbar'},button(t('common.default'),()=>scriptAction(id,'default',{version_id:v.id})),button(t('common.retire'),()=>scriptAction(id,'retire',{version_id:v.id}),'danger'))]);shell(el('div',{},pageHead(sampleText(s,'name'),sampleText(s,'description'),el('div',{'class':'toolbar'},button(t('common.edit'),()=>renderScriptForm(s)),button(s.archived?t('common.restore'):t('common.archive'),()=>scriptAction(id,'archive',{archived:!s.archived},true),'danger'),button(t('common.check'),()=>scriptAction(id,'check')),button(t('common.publish'),()=>scriptAction(id,'publish',null,true),'primary'))),el('section',{'class':'card form-card'},el('h2',{},t('scripts.versions')),versions.length?table([t('scripts.version'),t('common.status'),t('scripts.mode'),t('common.created'),t('common.actions')],rows):empty(t('common.empty'))),s.source?el('section',{'class':'card form-card'},el('h2',{},t('scripts.source')),el('pre',{'class':'logs'},s.source)):null));}catch{}}
async function scriptAction(id,action,body=null,confirmNeeded=false){if(confirmNeeded&&!confirm(t(action==='archive'?'scripts.archiveConfirm':'scripts.publishConfirm')))return;try{await request(`/api/scripts/${id}/${action}`,{method:'POST',body:body?jsonBody(body):undefined});showToast(t('common.success'));renderScriptDetail(id);}catch{}}

async function renderRuns(){
  const initial=new URLSearchParams(location.search);
  let tasks=[];try{tasks=await request('/api/tasks');}catch{}
  const status=el('select',{name:'status','aria-label':t('common.status')},el('option',{value:''},t('common.all')),...RUN_STATUSES.map(value=>el('option',{value,selected:initial.get('status')===value},t(`states.${value}`))));
  const trigger=el('select',{name:'trigger','aria-label':t('runs.trigger')},el('option',{value:''},t('common.all')),el('option',{value:'manual'},t('tasks.manual')),el('option',{value:'schedule'},t('tasks.schedule')));
  const taskSelect=el('select',{name:'task_id','aria-label':t('runs.task')},el('option',{value:''},t('common.all')),...tasks.map(task=>el('option',{value:task.id,selected:initial.get('task_id')===task.id},task.name)));
  const start=input('start',(initial.get('start')||'').slice(0,10),'date');start.setAttribute('aria-label',t('runs.start'));
  const end=input('end',(initial.get('end')||'').slice(0,10),'date');end.setAttribute('aria-label',t('runs.end'));
  const filters=el('form',{'class':'toolbar'},status,trigger,taskSelect,start,end,el('button',{'class':'button',type:'submit'},t('common.filter')));
  const results=el('div',{},empty(t('common.loading')));
  const content=el('div',{},pageHead(t('runs.title'),t('runs.subtitle')),el('div',{'class':'card form-card'},filters),results);
  shell(content);
  const load=async page=>{
    const q=new URLSearchParams(new FormData(filters));
    if(q.get('end'))q.set('end',endOfDay(q.get('end')));
    q.set('page',String(page));for(const [key,value] of [...q])if(!value)q.delete(key);
    history.replaceState({},'',`/runs?${q}`);
    try{
      state.runs=await request(`/api/executions?${q}`);
      const rows=(state.runs.items||[]).map(run=>[el('button',{'class':'link',onclick:()=>go(`/runs/${run.id}`)},run.task_name||run.id),badge(run.status),systemLabel(run.trigger),fmt(run.created_at),run.finished_at?fmt(run.finished_at):t('runs.live')]);
      clear(results);
      if(rows.length)results.append(table([t('runs.task'),t('common.status'),t('runs.trigger'),t('common.created'),t('runs.finished')],rows));else results.append(el('div',{'class':'card'},empty(t('runs.noRuns'))));
      const pages=Math.max(1,Math.ceil(state.runs.total/50));
      if(pages>1)results.append(el('div',{'class':'form-actions'},button(t('common.previous'),()=>load(Math.max(1,state.runs.page-1)),'',state.runs.page<=1),el('span',{},`${state.runs.page} / ${pages}`),button(t('common.next'),()=>load(Math.min(pages,state.runs.page+1)))));
    }catch{}
  };
  filters.addEventListener('submit',event=>{event.preventDefault();load(1);});
  load(Number(initial.get('page'))||1);
}
async function renderRunDetail(id){
  try{
    const run=await request(`/api/executions/${id}`);
    const out=el('pre',{'class':'logs','aria-live':'polite'},'');
    const err=el('pre',{'class':'logs','aria-live':'polite'},'');
    const statusSlot=el('div',{},badge(run.status));
    const warning=el('p',{'class':'error-text'});
    const controls=el('div',{'class':'toolbar'});
    let paused=false;let follow=true;
    const pauseButton=button(t('runs.pause'),()=>{paused=!paused;pauseButton.textContent=String(paused?t('runs.resume'):t('runs.pause'));});
    const followControl=el('label',{'class':'check'},el('input',{type:'checkbox',checked:true,onchange:event=>{follow=event.target.checked;}}),t('runs.follow'));
    controls.append(pauseButton,followControl);
    const artifacts=(run.artifacts||[]).map(a=>el('a',{'class':'button',href:`/api/executions/${id}/artifacts/${encodeURIComponent(a.name)}`},`${a.name} · ${a.size} B`));
    const cancel=canCancelExecution(run.status)?button(t('runs.cancelRun'),()=>cancelRun(id),'danger'):null;
    shell(el('div',{},
      pageHead(run.task_name||id,`${run.script_name||''} · ${systemLabel(run.trigger)}`,cancel),
      el('div',{'class':'grid'},
        el('div',{'class':'card stat span-4'},el('small',{},t('common.status')),statusSlot),
        el('div',{'class':'card stat span-4'},el('small',{},t('common.created')),el('strong',{},fmt(run.created_at))),
        el('div',{'class':'card stat span-4'},el('small',{},t('runs.exitCode')),el('strong',{},String(run.exit_code??'—')))),
      el('section',{'class':'card form-card'},el('h2',{},t('runs.snapshot')),el('pre',{'class':'logs'},JSON.stringify({version_id:run.version_id,timezone:run.timezone,params:run.params},null,2)),run.reason?el('p',{},`${t('runs.reason')}: ${run.reason}`):null),
      el('section',{'class':'card form-card'},controls,warning,el('h2',{},t('runs.stdout')),out,el('h2',{},t('runs.stderr')),err),
      el('section',{'class':'card form-card'},el('h2',{},t('runs.artifacts')),...(artifacts.length?artifacts:[el('p',{'class':'hint'},t('runs.noArtifacts'))]))));
    monitorRun(id,{out,err,statusSlot,warning,isPaused:()=>paused,shouldFollow:()=>follow,status:run.status});
  }catch{}
}
async function monitorRun(id,view,stdoutOffset=0,stderrOffset=0){
  if(route()!==`/runs/${id}`)return;
  try{
    if(!view.isPaused()){
      const logs=await request(`/api/executions/${id}/logs?stdout_offset=${stdoutOffset}&stderr_offset=${stderrOffset}`);
      view.out.textContent+=logs.stdout||'';view.err.textContent+=logs.stderr||'';
      stdoutOffset=logs.stdout_offset;stderrOffset=logs.stderr_offset;
      view.warning.textContent=logs.expired?String(t('runs.logsExpired')):logs.truncated?String(t('runs.logsTruncated')):'';
      if(view.shouldFollow()){view.out.scrollTop=view.out.scrollHeight;view.err.scrollTop=view.err.scrollHeight;}
    }
    const oldStatus=view.status;const current=await request(`/api/executions/${id}`);clear(view.statusSlot);view.statusSlot.append(badge(current.status));view.status=current.status;
    if(executionIsLive(oldStatus)&&!executionIsLive(current.status)){renderRunDetail(id);return;}
    if(executionIsLive(current.status))setTimeout(()=>monitorRun(id,view,stdoutOffset,stderrOffset),1500);
  }catch{}
}
async function cancelRun(id){if(!confirm(t('runs.cancelConfirm')))return;try{await request(`/api/executions/${id}/cancel`,{method:'POST'});renderRunDetail(id);}catch{}}

async function renderAdmin(section){if(state.bootstrap.user.role!=='admin')return go('/tasks');const tabs=el('div',{'class':'toolbar'},...['settings','users','variables','audit'].map(x=>button(t(`nav.${x}`),()=>go(`/admin/${x}`),section===x?'primary':'')));const wrap=el('div',{},pageHead(t('admin.title'),'',tabs),empty(t('common.loading')));shell(wrap);try{if(section==='settings')await adminSettings(wrap);else if(section==='users')await adminUsers(wrap);else if(section==='variables')await adminVariables(wrap);else await adminAudit(wrap);}catch{}}
async function adminSettings(wrap){const data=await request('/api/admin/settings');const workerReady=data.worker?.last_seen&&(Date.now()-new Date(data.worker.last_seen).getTime()<=45000);const health=el('div',{'class':'grid'},el('div',{'class':'card stat span-4'},el('small',{},t('admin.scheduler')),badge(data.scheduler?.status||'unavailable'),el('div',{'class':'hint'},fmt(data.scheduler?.last_tick))),el('div',{'class':'card stat span-4'},el('small',{},t('admin.worker')),badge(workerReady?'ready':'unavailable'),el('div',{'class':'hint'},fmt(data.worker?.last_seen))));const form=el('form',{'class':'card form-card'},el('div',{'class':'fields'},field(t('admin.timezone'),input('timezone',data.timezone,'text',true)),field(t('admin.concurrency'),input('concurrency',data.concurrency,'number',true)),field(t('admin.logRetention'),input('log_retention_days',data.log_retention_days,'number',true)),field(t('admin.metadataRetention'),input('metadata_retention_days',data.metadata_retention_days,'number',true))),el('div',{'class':'form-actions'},el('button',{'class':'button primary',type:'submit'},t('common.save'))));form.addEventListener('submit',async e=>{e.preventDefault();const d=Object.fromEntries(new FormData(form));d.concurrency=Number(d.concurrency);d.log_retention_days=Number(d.log_retention_days);d.metadata_retention_days=Number(d.metadata_retention_days);try{await request('/api/admin/settings',{method:'PATCH',body:jsonBody(d)});showToast(t('common.success'));}catch{}});wrap.lastChild.replaceWith(el('div',{},health,form));}
async function adminUsers(wrap){
  const users=await request('/api/admin/users');
  const editor=el('div');
  const openEditor=user=>{
    clear(editor);
    const username=input('username',user?.username||'','text',!user);username.disabled=Boolean(user);
    const password=input('password','','password',!user);
    const role=el('select',{name:'role'},el('option',{value:'operator',selected:user?.role!=='admin'},t('admin.operator')),el('option',{value:'admin',selected:user?.role==='admin'},t('admin.admin')));
    const enabled=el('input',{name:'enabled',type:'checkbox',checked:user?.enabled!==false});
    const form=el('form',{'class':'card form-card'},el('div',{'class':'fields'},field(t('auth.username'),username),field(t('auth.password'),password,user?t('common.optional'):''),field(t('admin.role'),role),el('label',{'class':'check'},enabled,t('admin.enabled'))),el('div',{'class':'form-actions'},button(t('common.cancel'),()=>clear(editor)),el('button',{'class':'button primary',type:'submit'},t('common.save'))));
    form.addEventListener('submit',async event=>{event.preventDefault();const data={username:username.value,password:password.value||undefined,role:role.value,enabled:enabled.checked};try{if(user)await request(`/api/admin/users/${user.id}`,{method:'PATCH',body:jsonBody({password:data.password,role:data.role,enabled:data.enabled})});else await request('/api/admin/users',{method:'POST',body:jsonBody(data)});renderAdmin('users');}catch{}});
    editor.append(form);username.focus();
  };
  const rows=users.map(user=>[user.username,badge(user.enabled?'active':'disabled'),systemLabel(user.role),button(t('common.edit'),()=>openEditor(user))]);
  wrap.lastChild.replaceWith(el('div',{},button(t('admin.userCreate'),()=>openEditor(null),'primary'),editor,table([t('auth.username'),t('common.status'),t('admin.role'),t('common.actions')],rows)));
}
async function adminVariables(wrap){
  const variables=await request('/api/admin/variables');
  const form=el('form',{'class':'card form-card'},
    el('div',{'class':'fields'},
      field(t('common.name'),input('name','','text',true)),
      field(t('admin.scope'),input('scope','instance','text',true)),
      field(t('admin.value'),input('value','','password',true),t('admin.variableHint'),true)),
    el('button',{'class':'button primary',type:'submit'},t('admin.variableCreate')));
  form.addEventListener('submit',async e=>{e.preventDefault();try{await request('/api/admin/variables',{method:'POST',body:jsonBody(Object.fromEntries(new FormData(form)))});renderAdmin('variables');}catch{}});
  const rows=variables.map(v=>[v.name,v.scope,fmt(v.updated_at),button(t('common.delete'),async()=>{if(confirm(t('admin.deleteVariable'))){await request(`/api/admin/variables/${v.id}`,{method:'DELETE'});renderAdmin('variables');}},'danger')]);
  wrap.lastChild.replaceWith(el('div',{},form,table([t('common.name'),t('admin.scope'),t('common.updated'),t('common.actions')],rows)));
}
async function adminAudit(wrap){const events=await request('/api/admin/audit');const rows=events.map(e=>[fmt(e.created_at),e.actor||'—',e.action,e.target||'—']);wrap.lastChild.replaceWith(events.length?table([t('common.created'),t('auth.username'),t('common.actions'),t('common.details')],rows):el('div',{'class':'card'},empty(t('admin.auditEmpty'))));}
function renderAccount(){const form=el('form',{'class':'card form-card'},el('div',{'class':'fields'},field(t('auth.currentPassword'),input('current_password','','password',true),'',true),field(t('auth.newPassword'),input('password','','password',true),t('errors.passwordLength'),true)),el('div',{'class':'form-actions'},el('button',{'class':'button primary',type:'submit'},t('common.save'))));form.addEventListener('submit',async e=>{e.preventDefault();try{await request('/api/me/password',{method:'POST',body:jsonBody(Object.fromEntries(new FormData(form)))});dirty=false;state.bootstrap.user=null;setCsrf(null);history.replaceState({},'','/');renderAuth(false);showToast(t('common.success'));}catch{}});trackDirty(form);shell(el('div',{},pageHead(t('nav.account'),state.bootstrap.user.username),form));}

bootstrap();
