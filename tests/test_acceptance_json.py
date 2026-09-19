"""Raw JSON transport must not silently change workflow intent."""
import pytest
from test_workflow_api_contract import client, admin, graph


@pytest.mark.parametrize('content_type', ['application/json', 'application/vnd.sleep-in+json'])
def test_duplicate_escaped_keys_rejected_before_workflow_write(client, content_type):
    admin(client)
    raw=b'{"name":"First","na\\u006de":"Second","nodes":[],"edges":[]}'
    response=client.post('/api/workflows',content=raw,headers={'Content-Type':content_type})
    assert response.status_code==400
    assert response.json()['detail']['code']=='duplicate_json_field'
    assert client.get('/api/workflows').json()==[]


def test_valid_nested_repeated_names_and_unicode_survive_body_replay(client):
    admin(client)
    data=graph();data['params']={'left':{'v':'你好🌙'},'right':{'v':0},'flag':False,'empty':None,'decimal':1.5}
    response=client.post('/api/workflows',json=data)
    assert response.status_code==200,response.text
    assert response.json()['params']==data['params']
    malformed=client.post('/api/workflows',content=b'{',headers={'Content-Type':'application/json'})
    assert malformed.status_code==422
    assert len(client.get('/api/workflows').json())==1


def test_chunked_json_over_quota_rejected_without_admission(client):
    admin(client)
    def chunks():
        yield b'{"name":"'
        for _ in range(27):yield b'x'*(1024*1024)
        yield b'"}'
    response=client.post('/api/workflows',content=chunks(),headers={'Content-Type':'application/json'})
    assert response.status_code==413
    assert client.get('/api/workflows').json()==[]


@pytest.mark.parametrize('raw,code', [
    (b'{"x":'+b'9'*5000+b'}','invalid_json'),
    (b'{"\\ud800":1,"\\ud800":2}','duplicate_json_field'),
],ids=['oversized-integer','surrogate-duplicate'])
def test_invalid_json_values_have_safe_error_without_write(client,raw,code):
    admin(client)
    response=client.post('/api/workflows',content=raw,headers={'Content-Type':'application/json'})
    assert response.status_code==400,response.text
    assert response.json()['detail']['code']==code
    assert client.get('/api/workflows').json()==[]


@pytest.mark.parametrize('number',[b'NaN',b'Infinity',b'-Infinity',b'1e9999'])
def test_nonfinite_workflow_parameters_rejected_without_write(client,number):
    admin(client)
    raw=b'{"name":"Bad numeric input","params":{"value":'+number+b'},"nodes":[],"edges":[]}'
    response=client.post('/api/workflows',content=raw,headers={'Content-Type':'application/json'})
    assert response.status_code==400,response.text
    assert response.json()['detail']['code']=='invalid_json'
    assert client.get('/api/workflows').json()==[]


@pytest.mark.parametrize('params',[b'{"value":"\\ud800"}',b'{"\\udfff":1}',b'{"nested":["\\ud800"]}'])
def test_unpaired_surrogates_never_poison_saved_workflows(client,params):
    admin(client)
    response=client.post('/api/workflows',content=b'{"name":"Unicode boundary","nodes":[],"edges":[],"params":'+params+b'}',headers={'Content-Type':'application/json'})
    assert response.status_code==400,response.text
    assert client.get('/api/workflows').json()==[]
    assert response.json()['detail']['code']=='invalid_json'
