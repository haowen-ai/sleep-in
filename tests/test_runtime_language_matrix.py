"""49 ordered pairs execute real workers; no mocked language process or SQL receiver."""
import copy
import itertools
import json
import os
from pathlib import Path
import pytest
from taskconsole.store import Store
from taskconsole.workflows_packs import RuntimePacks,prepare_node
from taskconsole.workflows_runtime import run_script,resolve_path
from taskconsole.workflows_sql import create_connection,execute_sql

LANGUAGES=['sql','python','javascript','shell','java','c','cpp']
VALUE={'order_id':'A002','amount':'20.25','region':None,'unicode':'华东 🚀','count':3,'empty':''}
SQL="SELECT 'A002' AS order_id, '20.25' AS amount, NULL AS region, '华东 🚀' AS unicode, 3 AS count, '' AS empty"


def producer(language):
    encoded=json.dumps({'schemaVersion':1,'data':VALUE,'artifacts':[]},ensure_ascii=False)
    if language=='python':return 'def main(inputs): return '+repr(VALUE)
    if language=='javascript':return 'function main(inputs){return '+json.dumps(VALUE,ensure_ascii=False)+';}'
    if language=='shell':return 'cat > "$SLEEP_IN_OUTPUT_FILE" <<\'SLEEPIN_END\'\n'+encoded+'\nSLEEPIN_END\n'
    if language=='java':return 'import java.nio.file.*;public class Main{public static void main(String[]a)throws Exception{Files.writeString(Path.of(System.getenv("SLEEP_IN_OUTPUT_FILE")),'+json.dumps(encoded,ensure_ascii=False)+');}}'
    if language in {'c','cpp'}:return '#include <stdio.h>\n#include <stdlib.h>\nint main(void){FILE*f=fopen(getenv("SLEEP_IN_OUTPUT_FILE"),"w");if(!f)return 1;fputs('+json.dumps(encoded,ensure_ascii=False)+',f);return fclose(f);}'
    return SQL


def consumer(language):
    if language=='python':return 'def main(inputs): return inputs'
    if language=='javascript':return 'async function main(inputs){return inputs;}'
    if language=='shell':return 'printf \'{"schemaVersion":1,"data":\' > "$SLEEP_IN_OUTPUT_FILE"\ncat "$SLEEP_IN_INPUT_FILE" >> "$SLEEP_IN_OUTPUT_FILE"\nprintf \',"artifacts":[]}\' >> "$SLEEP_IN_OUTPUT_FILE"'
    if language=='java':return 'import java.nio.file.*;public class Main{public static void main(String[]a)throws Exception{String data=Files.readString(Path.of(System.getenv("SLEEP_IN_INPUT_FILE")));Files.writeString(Path.of(System.getenv("SLEEP_IN_OUTPUT_FILE")),"{\\"schemaVersion\\":1,\\"data\\":"+data+",\\"artifacts\\":[]}");}}'
    if language in {'c','cpp'}:return '#include <stdio.h>\n#include <stdlib.h>\nint main(void){FILE*i=fopen(getenv("SLEEP_IN_INPUT_FILE"),"r"),*o=fopen(getenv("SLEEP_IN_OUTPUT_FILE"),"w");if(!i||!o)return 1;fputs("{\\"schemaVersion\\":1,\\"data\\":",o);int c;while((c=fgetc(i))!=EOF)fputc(c,o);fputs(",\\"artifacts\\":[]}",o);fclose(i);return fclose(o);}'
    return 'SELECT '+', '.join(':'+key+' AS '+key for key in VALUE)


@pytest.fixture(scope='module')
def matrix(tmp_path_factory):
    java=os.environ.get('SLEEP_IN_TEST_JAVAC')
    if not java:pytest.skip('Set SLEEP_IN_TEST_JAVAC to an integrity-verified JDK for the real seven-language matrix')
    root=tmp_path_factory.mktemp('real-language-matrix');store=Store(root,'sqlite:///'+str(root/'state.sqlite'))
    connection=create_connection(store,{'dialect':'sqlite','config':{'synthetic':True}})
    versions={};packs=RuntimePacks(store)
    for language in LANGUAGES:
        if language=='sql':continue
        config={'executable':java} if language=='java' else {}
        profile=packs.create({'name':'Matrix '+language,'language':language,'config':config});version=packs.build(profile['id'])
        assert version['status']=='ready',version
        versions[language]=version['id']
    def make(language,nid,source):
        config={'connection_id':connection['id'],'dialect':'sqlite','mode':'query'} if language=='sql' else {'runtime_version_id':versions[language]}
        return prepare_node(store,{'id':nid,'name':nid,'kind':language,'source':source,'config':config,'inputs':{}})
    prepared={(lang,role):make(lang,role,producer(lang) if role=='producer' else consumer(lang)) for lang in LANGUAGES for role in ['producer','consumer']}
    yield store,prepared
    store.engine.dispose()


@pytest.mark.parametrize('source,target',list(itertools.product(LANGUAGES,repeat=2)),ids=lambda value:value)
def test_real_ordered_language_pair(matrix,source,target,tmp_path):
    store,nodes=matrix
    def execute(node,inputs,directory):
        return execute_sql(store,node,inputs,'matrix') if node['kind']=='sql' else run_script(node,inputs,directory,store.path)
    first=execute(nodes[(source,'producer')],{},tmp_path/'source')
    assert first['status']=='succeeded',first
    source_data=first['output']['data']
    inputs={key:resolve_path(source_data,['rows',0,key] if source=='sql' else [key]) for key in VALUE}
    assert inputs==VALUE
    second=execute(nodes[(target,'consumer')],inputs,tmp_path/'target')
    assert second['status']=='succeeded',second
    data=second['output']['data']['rows'][0] if target=='sql' else second['output']['data']
    assert data==VALUE
    assert data['region'] is None and type(data['count']) is int


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='Native n8n required')
@pytest.mark.parametrize('source',LANGUAGES)
def test_actual_n8n_all_target_pairs(matrix,source,monkeypatch):
    import socket,threading,time,uvicorn
    from fastapi import FastAPI
    from taskconsole.workflows import register_workflow_routes
    from taskconsole.workflows_n8n import execute_graph
    store,prepared=matrix;app=FastAPI();service=register_workflow_routes(app,store,lambda request,tx,admin=False:{'id':'matrix','role':'admin'})
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
    deadline=time.time()+10
    while not server.started and time.time()<deadline:time.sleep(.05)
    monkeypatch.setenv('SLEEP_IN_BASE_URL',f'http://127.0.0.1:{port}')
    try:
        first=copy.deepcopy(prepared[(source,'producer')]);first['id']='source';nodes=[first];edges=[]
        for target in LANGUAGES:
            node=copy.deepcopy(prepared[(target,'consumer')]);node['id']='to_'+target
            node['inputs']={key:{'source':'node','node_id':'source','path':['rows',0,key] if source=='sql' else [key]} for key in VALUE}
            nodes.append(node);edges.append({'source':'source','target':node['id']})
        wf=service.save({'name':'Actual '+source+' to all seven languages','nodes':nodes,'edges':edges});service.publish(wf['id']);run=service.admit(wf['id'])
        execute_graph(service,run['id']);result=service.get_run(run['id'])
        assert result['status']=='succeeded',(result.get('error'),result.get('adapter_log'),result['nodes'])
        assert result.get('n8n_execution_id')
        for target in LANGUAGES:
            output=result['nodes']['to_'+target]['output']['data'];actual=output['rows'][0] if target=='sql' else output
            assert actual==VALUE,(source,target,actual)
    finally:server.should_exit=True;thread.join(timeout=10)
