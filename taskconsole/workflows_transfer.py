"""Admin-owned, source-inclusive portable templates with no instance bindings.

This exports explicit user-authored text/literals. It cannot identify arbitrary
credentials or hostnames an author has deliberately embedded inside those values.
"""
import copy
import json
import math
from fastapi import HTTPException, Request
from .workflows import WorkflowService, WorkflowError, IDENTIFIER, ancestors, need
from .workflows_runtime import LANGUAGES, validate_schema, path_tokens
from .workflows_sql import DIALECTS

LIMIT=1024*1024
WORKFLOW_FIELDS={'name','description','nodes','edges','params','parameter_schema','timezone','timeout'}
NODE_FIELDS={'id','name','kind','source','config','inputs','outputs','input_schema','position'}
CONFIG_FIELDS={'dialect','mode','entry_mode','main_class','join','merge','merge_target','timeout','statements','connection_requirement'}
BINDING_FIELDS={'source','path','node_id','value','optional','default','type'}
EDGE_FIELDS={'source','target','required','condition'}
NOTICE={'user_source_and_literals_included':True,'message':'Review before sharing: source code, names, descriptions, schemas, parameters, constant/default values, SQL statement bindings and conditions are included verbatim. Remove any private literals you authored. Configured connections, runtime bindings and execution history are excluded.'}


def invalid(message):
    raise WorkflowError(message,'invalid_template')


def member(value, choices):
    return isinstance(value,str) and value in choices


def object_fields(value,allowed,label):
    if not isinstance(value,dict):invalid(label+' must be an object')
    if set(value)-allowed:invalid(label+' contains unsupported or instance-specific fields')


def bounded_json(value):
    try:encoded=json.dumps(value,ensure_ascii=False,allow_nan=False).encode('utf-8')
    except (TypeError,ValueError,RecursionError):invalid('Template must contain finite JSON values')
    if len(encoded)>LIMIT:invalid('Template exceeds 1 MiB')


def sanitize_config(config):
    result={k:copy.deepcopy(v) for k,v in config.items() if k in CONFIG_FIELDS and k!='connection_requirement'}
    if 'statements' in result:
        result['statements']=[{k:copy.deepcopy(v) for k,v in statement.items() if k in {'sql','bindings'}} for statement in result['statements']]
    return result


def export_template(store,workflow_id):
    with store.transaction() as tx:workflow=need(tx,'workflow',workflow_id)
    clean={k:copy.deepcopy(v) for k,v in workflow.items() if k in WORKFLOW_FIELDS and k not in {'nodes','edges'}}
    clean['nodes']=[];clean['edges']=[];connections=[];runtime_nodes={};connection_keys={}
    for node in workflow.get('nodes',[]):
        clean_node={k:copy.deepcopy(v) for k,v in node.items() if k in NODE_FIELDS and k not in {'config','inputs','position'}}
        config=node.get('config',{})
        clean_node['config']=sanitize_config(config)
        clean_node['inputs']={name:{key:copy.deepcopy(value) for key,value in binding.items() if key in BINDING_FIELDS} for name,binding in node.get('inputs',{}).items()}
        if 'position' in node:clean_node['position']={k:copy.deepcopy(v) for k,v in node['position'].items() if k in {'x','y'}}
        if node.get('kind')=='sql':
            identity=(config.get('connection_id') or node['id'],config.get('dialect','sqlite'))
            if identity not in connection_keys:
                key='connection-'+str(len(connections)+1);connection_keys[identity]=key
                connections.append({'key':key,'name':'SQL connection '+str(len(connections)+1),'dialect':identity[1],'node_ids':[],'write_required':False})
            requirement=next(item for item in connections if item['key']==connection_keys[identity])
            requirement['node_ids'].append(node['id'])
            requirement['write_required']|=config.get('mode','query')=='write'
            clean_node['config']['connection_requirement']=requirement['key']
        else:runtime_nodes.setdefault(node['kind'],[]).append(node['id'])
        clean['nodes'].append(clean_node)
    for edge in workflow.get('edges',[]):
        item={k:copy.deepcopy(v) for k,v in edge.items() if k in EDGE_FIELDS and k!='condition'}
        if edge.get('condition') is not None:item['condition']={k:copy.deepcopy(v) for k,v in edge['condition'].items() if k in {'path','operator','value'}}
        clean['edges'].append(item)
    template={'schemaVersion':1,'workflow':clean,'requirements':{'connections':connections,'runtimes':[{'language':language,'node_ids':nodes} for language,nodes in runtime_nodes.items()]},'review_notice':copy.deepcopy(NOTICE)}
    validate_template(template)
    return template


def validate_template(template):
    bounded_json(template)
    object_fields(template,{'schemaVersion','workflow','requirements','review_notice'},'Template')
    if type(template.get('schemaVersion')) is not int or template['schemaVersion']!=1:invalid('Only template schemaVersion 1 is supported')
    workflow=template.get('workflow');object_fields(workflow,WORKFLOW_FIELDS,'Workflow')
    if not isinstance(workflow.get('name'),str) or not workflow['name'].strip():invalid('Workflow name is required')
    for field in ('description','timezone'):
        if field in workflow and not isinstance(workflow[field],str):invalid(field+' must be text')
    if not isinstance(workflow.get('params',{}),dict):invalid('Parameters must be an object')
    if 'timeout' in workflow and (type(workflow['timeout']) is not int or not 1<=workflow['timeout']<=86400):invalid('Timeout must be an integer from 1 to 86400')
    try:validate_schema(workflow.get('parameter_schema',{}))
    except ValueError as exc:invalid(str(exc))
    nodes=workflow.get('nodes');edges=workflow.get('edges')
    if not isinstance(nodes,list) or not 1<=len(nodes)<=50:invalid('Template requires 1–50 nodes')
    if not isinstance(edges,list) or len(edges)>2500:invalid('Edges must be a bounded array')
    ids=set()
    for node in nodes:
        object_fields(node,NODE_FIELDS,'Node')
        if not isinstance(node.get('id'),str) or not IDENTIFIER.fullmatch(node['id']) or node['id'] in ids:invalid('Node IDs must be unique valid identifiers')
        ids.add(node['id'])
        if node.get('kind') not in LANGUAGES:invalid('Unsupported node language')
        if not isinstance(node.get('source',''),str) or not isinstance(node.get('name',''),str):invalid('Node name/source must be text')
        config=node.get('config',{});object_fields(config,CONFIG_FIELDS,'Node config')
        for field,allowed in {'dialect':DIALECTS,'mode':{'query','write'},'entry_mode':{'function','file'},'join':{'all','any'},'merge':{'named','append'}}.items():
            if field in config and (not isinstance(config[field],str) or config[field] not in allowed):invalid('Unsupported '+field)
        for field in ('main_class','merge_target','connection_requirement'):
            if field in config and (not isinstance(config[field],str) or not IDENTIFIER.fullmatch(config[field])):invalid('Invalid '+field)
        if 'timeout' in config and (type(config['timeout']) is not int or not 1<=config['timeout']<=3600):invalid('Invalid node timeout')
        if 'statements' in config:
            if not isinstance(config['statements'],list) or not config['statements']:invalid('Statements must be a nonempty array')
            for statement in config['statements']:
                object_fields(statement,{'sql','bindings'},'SQL statement')
                if not isinstance(statement.get('sql'),str) or not isinstance(statement.get('bindings',{}),dict):invalid('Invalid SQL statement/bindings')
        if 'position' in node:
            object_fields(node['position'],{'x','y'},'Position')
            if set(node['position'])!={'x','y'} or any(type(v) not in (int,float) or not math.isfinite(v) for v in node['position'].values()):invalid('Position requires finite x/y coordinates')
        inputs=node.get('inputs',{})
        if not isinstance(inputs,dict):invalid('Inputs must be a named object')
        for binding in inputs.values():
            object_fields(binding,BINDING_FIELDS,'Binding')
            if not member(binding.get('source'),{'constant','node','parameter','context','none'}):invalid('Unsupported binding source; rebind private credentials explicitly')
            try:path_tokens(binding.get('path'))
            except ValueError as exc:invalid(str(exc))
        for field in ('outputs','input_schema'):
            try:validate_schema(node.get(field,{}))
            except ValueError as exc:invalid(str(exc))
    for edge in edges:
        object_fields(edge,EDGE_FIELDS,'Edge')
        if not member(edge.get('source'),ids) or not member(edge.get('target'),ids):invalid('Edge references an unknown node')
        if 'required' in edge and type(edge['required']) is not bool:invalid('Edge required must be boolean')
        if 'condition' in edge:
            object_fields(edge['condition'],{'path','operator','value'},'Condition')
            if not member(edge['condition'].get('operator'),{'eq','ne','gt','gte','lt','lte','truthy'}):invalid('Invalid condition operator')
    try:ancestors(nodes,edges)
    except ValueError as exc:invalid(str(exc))
    for node in nodes:
        for binding in node.get('inputs',{}).values():
            if binding.get('source')=='node' and not member(binding.get('node_id'),ids):invalid('Binding references an unknown node')
    requirements=template.get('requirements',{});object_fields(requirements,{'connections','runtimes'},'Requirements')
    known={}
    for field in ('connections','runtimes'):
        if not isinstance(requirements.get(field,[]),list):invalid(field+' requirements must be an array')
    for requirement in requirements.get('connections',[]):
        object_fields(requirement,{'key','name','dialect','node_ids','write_required'},'Connection requirement')
        key=requirement.get('key')
        if not isinstance(key,str) or not IDENTIFIER.fullmatch(key) or key in known:invalid('Invalid connection requirement key')
        if not member(requirement.get('dialect'),DIALECTS) or not isinstance(requirement.get('name'),str):invalid('Invalid connection requirement')
        if not isinstance(requirement.get('node_ids'),list) or any(not member(n,ids) for n in requirement['node_ids']):invalid('Unknown connection requirement node')
        if type(requirement.get('write_required')) is not bool:invalid('write_required must be boolean')
        known[key]=requirement
    for node in nodes:
        key=node.get('config',{}).get('connection_requirement')
        if key and (key not in known or node['id'] not in known[key]['node_ids'] or node['kind']!='sql' or node.get('config',{}).get('dialect','sqlite')!=known[key]['dialect']):invalid('Connection requirement does not match node')
    for requirement in requirements.get('runtimes',[]):
        object_fields(requirement,{'language','node_ids'},'Runtime requirement')
        if requirement.get('language') not in LANGUAGES or not isinstance(requirement.get('node_ids'),list) or any(not member(n,ids) for n in requirement['node_ids']):invalid('Invalid runtime requirement')
    return workflow


def import_template(store,template):
    workflow=copy.deepcopy(validate_template(template))
    for node in workflow['nodes']:
        node.setdefault('config',{}).pop('connection_requirement',None)
    workflow.update(enabled=False,schedule={'kind':'manual'},triggers=[])
    result=WorkflowService(store).save(workflow)
    with store.transaction() as tx:
        saved=tx.get('workflow',result['id'])
        saved['import_requirements']=copy.deepcopy(template.get('requirements',{}))
        saved['requires_rebinding']=True
        tx.put('workflow',saved)
    return saved


def register_transfer_routes(app,store,require):
    def admin(request):
        with store.transaction() as tx:require(request,tx,admin=True)

    @app.get('/api/workflows/{wid}/export')
    def export(wid:str,request:Request):
        admin(request)
        return export_template(store,wid)

    @app.post('/api/workflow-import')
    def import_workflow(request:Request,body:dict):
        admin(request)
        if set(body)!={'template'}:raise HTTPException(422,detail={'code':'invalid_template','message':'Expected a template object'})
        return import_template(store,body['template'])
