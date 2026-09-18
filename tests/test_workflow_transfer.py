"""Portable template boundaries; all inputs and credentials are synthetic canaries."""
import copy
import json
import pytest
from fastapi.testclient import TestClient
from taskconsole.app import create_app
from taskconsole.store import Store
from taskconsole.workflows import WorkflowService
from taskconsole.workflows_sql import create_connection


@pytest.fixture
def service(tmp_path):
    store=Store(tmp_path,f'sqlite:///{tmp_path}/db.sqlite')
    yield WorkflowService(store)
    store.engine.dispose()


def authored_graph():
    return {'name':'Portable synthetic graph','description':'Review literals before sharing','timezone':'America/Chicago',
        'params':{'literal':'user-authored-value','nested':{'_runtime':'ordinary business field'}},
        'nodes':[
            {'id':'query','name':'Rows','kind':'sql','source':'SELECT :region AS region','config':{'dialect':'postgresql','connection_id':'private-id','mode':'query'},'inputs':{'region':{'source':'parameter','path':['literal']}},'position':{'x':30.5,'y':80},'outputs':{'type':'object'}},
            {'id':'script','name':'Transform','kind':'python','source':'def main(inputs):\n    return {"result": inputs["rows"]}\n','config':{'entry_mode':'function','runtime_id':'private-runtime'},'inputs':{'rows':{'source':'node','node_id':'query','path':['rows'],'optional':True,'default':[]},'constant':{'source':'constant','value':{'host':'literal.example','token':'user-authored-literal'}}},'position':{'x':320,'y':80},'outputs':{'type':'object'}}],
        'edges':[{'source':'query','target':'script','required':False}], 'enabled':False,
        'schedule':{'kind':'manual'},'timeout':300}


def test_export_excludes_configured_secrets_paths_ids_and_history(service):
    from taskconsole.workflows_transfer import export_template
    connection=create_connection(service.store,{'name':'secret-host.canary','dialect':'postgresql','config':{'host':'secret-host.canary','password':'SECRET_CANARY'}})
    data=authored_graph();data['nodes'][0]['config']['connection_id']=connection['id']
    workflow=service.save(data)
    with service.store.transaction() as tx:
        stored=tx.get('workflow',workflow['id'])
        stored.update(published_version_id='PRIVATE_PUBLICATION',triggers=[{'token':'PRIVATE_TRIGGER'}],history=['PRIVATE_HISTORY'],callback_token='PRIVATE_CALLBACK')
        stored['nodes'][1].update(_runtime={'executable':'/private/managed/python'},_build={'argv':['/private/compiled']},preview={'secret':'PRIVATE_PREVIEW'})
        stored['nodes'][0]['config'].update(password='CONFIG_SECRET',host='configured-host.canary',path='/private/config/path')
        tx.put('workflow',stored)
    result=export_template(service.store,workflow['id'])
    encoded=json.dumps(result)
    for canary in ('SECRET_CANARY','secret-host.canary',connection['id'],'PRIVATE_PUBLICATION','PRIVATE_TRIGGER','PRIVATE_HISTORY','PRIVATE_CALLBACK','/private/managed','/private/compiled','PRIVATE_PREVIEW','CONFIG_SECRET','configured-host.canary','/private/config/path','private-runtime'):
        assert canary not in encoded,canary
    assert result['schemaVersion']==1
    assert result['workflow']['nodes'][0]['config']['connection_requirement']=='connection-1'
    assert result['requirements']['connections'][0]['dialect']=='postgresql'
    assert result['review_notice']['user_source_and_literals_included'] is True
    assert 'user-authored-literal' in encoded and 'literal.example' in encoded


def test_roundtrip_retains_graph_but_requires_rebinding_and_publication(service):
    from taskconsole.workflows_transfer import export_template,import_template
    original=service.save(authored_graph())
    portable=export_template(service.store,original['id'])
    imported=import_template(service.store,portable)
    assert imported['id']!=original['id']
    assert imported['enabled'] is False
    assert imported['published_version_id'] is None
    assert imported.get('triggers',[])==[]
    assert imported['schedule']=={'kind':'manual'}
    assert imported['params']==original['params']
    assert imported['edges']==original['edges']
    for old,new in zip(original['nodes'],imported['nodes']):
        for key in ('id','name','kind','source','inputs','outputs','position'):assert new[key]==old[key]
        for key in ('connection_id','runtime_id','connection_requirement','_runtime','_build'):assert key not in new['config'] and key not in new
    assert any('connection' in error['message'].lower() for error in imported['validation_errors'])
    with service.store.transaction() as tx:
        assert tx.all('workflow_run')==[]
        assert tx.all('workflow_version')==[]


@pytest.mark.parametrize('target,key,value',[
    ('node','_runtime',{'executable':'/evil'}),('node','_build',{'argv':['/evil']}),
    ('node','callback_token','evil'),('config','runtime_id','private'),('config','connection_id','private'),
    ('workflow','published_version_id','private'),('workflow','triggers',[{'enabled':True}]),
])
def test_import_rejects_execution_metadata_injection_without_creating_draft(service,target,key,value):
    from taskconsole.workflows_transfer import export_template,import_template
    original=service.save(authored_graph());template=export_template(service.store,original['id'])
    destination=template['workflow'] if target=='workflow' else template['workflow']['nodes'][0] if target=='node' else template['workflow']['nodes'][0]['config']
    destination[key]=value
    with pytest.raises(ValueError):import_template(service.store,template)
    with service.store.transaction() as tx:assert len(tx.all('workflow'))==1


@pytest.mark.parametrize('mutate',[
    lambda t:t.update(schemaVersion=2),lambda t:t.update(schemaVersion=True),
    lambda t:t['workflow'].update(nodes='invalid'),lambda t:t['workflow'].update(nodes=[None]),
    lambda t:t['workflow'].update(params=[]),lambda t:t['workflow']['nodes'][0].update(position={'x':float('nan'),'y':0}),
    lambda t:t['workflow'].update(description='x'*(1024*1024)),
])
def test_import_validates_version_shape_and_size_before_writing(service,mutate):
    from taskconsole.workflows_transfer import export_template,import_template
    original=service.save(authored_graph());template=export_template(service.store,original['id']);mutate(template)
    with pytest.raises(ValueError):import_template(service.store,template)
    with service.store.transaction() as tx:assert len(tx.all('workflow'))==1


def test_unknown_or_duplicate_node_ids_and_broken_edges_rejected(service):
    from taskconsole.workflows_transfer import export_template,import_template
    original=service.save(authored_graph());template=export_template(service.store,original['id'])
    template['workflow']['nodes'][1]['id']='query'
    with pytest.raises(ValueError):import_template(service.store,template)
    template=export_template(service.store,original['id']);template['workflow']['edges'][0]['target']='missing'
    with pytest.raises(ValueError):import_template(service.store,template)


def test_import_export_http_are_admin_and_csrf_protected(tmp_path,monkeypatch):
    monkeypatch.delenv('SLEEP_IN_LOCAL',raising=False)
    app=create_app(tmp_path,f'sqlite:///{tmp_path}/api.sqlite')
    with TestClient(app) as client:
        setup=client.post('/api/setup',json={'token':(tmp_path/'setup-token').read_text(),'username':'owner','password':'owner transfer password','timezone':'UTC'})
        client.headers['X-CSRF-Token']=setup.json()['csrf']
        workflow=client.post('/api/workflows',json=authored_graph()).json()
        export=client.get('/api/workflows/'+workflow['id']+'/export')
        assert export.status_code==200,export.text
        template=export.json()
        result=client.post('/api/workflow-import',json={'template':template})
        assert result.status_code==200,result.text
        assert result.json()['id']!=workflow['id']
        del client.headers['X-CSRF-Token']
        assert client.post('/api/workflow-import',json={'template':template}).status_code==403
        client.headers['X-CSRF-Token']=setup.json()['csrf']
        client.post('/api/admin/users',json={'username':'operator','password':'operator transfer password','role':'operator'})
        client.post('/api/logout')
        login=client.post('/api/login',json={'username':'operator','password':'operator transfer password'})
        client.headers['X-CSRF-Token']=login.json()['csrf']
        assert client.get('/api/workflows/'+workflow['id']+'/export').status_code==403
        assert client.post('/api/workflow-import',json={'template':template}).status_code==403
        client.post('/api/logout')
        assert client.get('/api/workflows/'+workflow['id']+'/export').status_code==401


@pytest.mark.parametrize('mutate',[
    lambda t:t['workflow']['nodes'][0].update(kind=[]),
    lambda t:t['workflow']['edges'][0].update(source={}),
    lambda t:t['requirements']['connections'][0].update(dialect=[]),
    lambda t:t['requirements']['connections'][0].update(node_ids=[{}]),
    lambda t:t['requirements']['runtimes'][0].update(language={}),
    lambda t:t['workflow']['nodes'][0]['inputs']['region'].update(source=[]),
])
def test_malformed_nested_identifiers_are_controlled_validation_errors(service,mutate):
    from taskconsole.workflows_transfer import export_template,import_template
    original=service.save(authored_graph());template=export_template(service.store,original['id']);mutate(template)
    with pytest.raises(ValueError):import_template(service.store,template)
