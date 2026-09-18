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
    connection=create_connection(store,{'dialect':'sqlite','config':{'synthetic':True},'write_enabled':True})
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

# Fixed-design acceptance uses complete F-ORDERS and independently observed
# business calls. Shell explicitly depends on /usr/bin/awk; native C/Java use
# constrained fixture parsers for amount strings, not a substituted interpreter.
ORDERS=[{'order_id':'A001','amount':'10.50','region':'华东'}, {'order_id':'A002','amount':'20.25','region':None}, {'order_id':'A003','amount':'0.00','region':'西部'}]
FTYPES={'text':'中文 / café / 🙂 / quote:" / slash:\\ / newline:\n','emptyText':'','zero':0,'negative':-7,'fraction':1.25,'flag':False,'nil':None,'emptyArray':[],'emptyObject':{},'decimal':'9007199254740993.01','largeInteger':'9007199254740993','timestamp':'2026-09-17T07:30:00+08:00','rows':[{'id':1,'value':None},{'id':2,'value':''}]}


def fixture_program(language,mode,payload=None):
    """Actual native file-protocol program; all parsing reads actual input bytes."""
    envelope=json.dumps({'schemaVersion':1,'data':payload or {},'artifacts':[]},ensure_ascii=False)
    if language=='python':
        pre='import os,json,pathlib\npathlib.Path("business-calls").open("a").write("1\\n")\n'
        if mode=='fail':return pre+'raise RuntimeError("expected source failure")'
        action='result='+repr(payload or {})
        if mode=='summary':action='from decimal import Decimal\ni=json.load(open(os.environ["SLEEP_IN_INPUT_FILE"]))\nr=i["orders"]\nresult={"summary":{"count":len(r),"total":format(sum((Decimal(x["amount"]) for x in r),Decimal("0")),".2f")}}'
        if mode=='types':action='result={"received":json.load(open(os.environ["SLEEP_IN_INPUT_FILE"]))["payload"]}'
        return pre+action+'\njson.dump({"schemaVersion":1,"data":result,"artifacts":[]},open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"),ensure_ascii=False)'
    if language=='javascript':
        pre='const fs=require("fs");fs.appendFileSync("business-calls","1\\n");'
        if mode=='fail':return pre+'throw Error("expected source failure");'
        action='let result='+json.dumps(payload or {},ensure_ascii=False)+';'
        if mode=='summary':action='const i=JSON.parse(fs.readFileSync(process.env.SLEEP_IN_INPUT_FILE,"utf8"));const cents=i.orders.reduce((n,r)=>n+BigInt(r.amount.replace(".","")),0n);let result={summary:{count:i.orders.length,total:String(cents/100n)+"."+String(cents%100n).padStart(2,"0")}};'
        if mode=='types':action='let result={received:JSON.parse(fs.readFileSync(process.env.SLEEP_IN_INPUT_FILE,"utf8")).payload};'
        return pre+action+'fs.writeFileSync(process.env.SLEEP_IN_OUTPUT_FILE,JSON.stringify({schemaVersion:1,data:result,artifacts:[]}));'
    if language=='shell':
        pre='printf "1\\n" >> business-calls\n'
        if mode=='fail':return pre+'exit 1\n'
        if mode=='summary':return pre+'''/usr/bin/awk 'BEGIN {count=0; cents=0} {s=$0; while(match(s,/"amount"[ ]*:[ ]*"[0-9]+\\.[0-9][0-9]"/)){x=substr(s,RSTART,RLENGTH); sub(/^.*:[ ]*"/,"",x);sub(/"$/,"",x);gsub(/\\./,"",x);cents+=x;count++;s=substr(s,RSTART+RLENGTH)}} END{printf "{\\"schemaVersion\\":1,\\"data\\":{\\"summary\\":{\\"count\\":%d,\\"total\\":\\"%d.%02d\\"}},\\"artifacts\\":[]}",count,int(cents/100),cents%100}' "$SLEEP_IN_INPUT_FILE" > "$SLEEP_IN_OUTPUT_FILE"
'''
        if mode=='types':return pre+'''printf '{"schemaVersion":1,"data":{"received":' > "$SLEEP_IN_OUTPUT_FILE"
# Input is exactly one payload property; retain all JSON bytes inside it.
sed 's/^{"payload": *//; s/}$//' "$SLEEP_IN_INPUT_FILE" >> "$SLEEP_IN_OUTPUT_FILE"
printf '},"artifacts":[]}' >> "$SLEEP_IN_OUTPUT_FILE"
'''
        return pre+'cat > "$SLEEP_IN_OUTPUT_FILE" <<\'END_FIXTURE\'\n'+envelope+'\nEND_FIXTURE\n'
    if language=='java':
        pre='import java.nio.file.*;import java.math.*;import java.util.regex.*;public class Main{public static void main(String[]a)throws Exception{Files.writeString(Path.of("business-calls"),"1\\n",StandardOpenOption.CREATE,StandardOpenOption.APPEND);'
        if mode=='fail':return pre+'throw new RuntimeException("expected source failure");}}'
        action='String out='+json.dumps(envelope,ensure_ascii=False)+';'
        if mode=='summary':action=r'''String input=Files.readString(Path.of(System.getenv("SLEEP_IN_INPUT_FILE")));Matcher m=Pattern.compile("\"amount\"\\s*:\\s*\"([0-9]+\\.[0-9]{2})\"").matcher(input);BigDecimal total=new BigDecimal("0.00");int count=0;while(m.find()){count++;total=total.add(new BigDecimal(m.group(1)));}String out="{\"schemaVersion\":1,\"data\":{\"summary\":{\"count\":"+count+",\"total\":\""+total.toPlainString()+"\"}},\"artifacts\":[]}";'''
        if mode=='types':action='String input=Files.readString(Path.of(System.getenv("SLEEP_IN_INPUT_FILE")));String value=input.substring(input.indexOf(":")+1,input.lastIndexOf("}"));String out="{\\"schemaVersion\\":1,\\"data\\":{\\"received\\":"+value+"},\\"artifacts\\":[]}";'
        return pre+action+'Files.writeString(Path.of(System.getenv("SLEEP_IN_OUTPUT_FILE")),out);}}'
    pre='#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\nint main(void){FILE *c=fopen("business-calls","a");fputs("1\\n",c);fclose(c);'
    if mode=='fail':return pre+'return 1;}'
    action='fputs('+json.dumps(envelope,ensure_ascii=False)+',o);'
    if mode=='summary':action=r'''char buf[65536];FILE*i=fopen(getenv("SLEEP_IN_INPUT_FILE"),"r");size_t n=fread(buf,1,sizeof(buf)-1,i);buf[n]=0;fclose(i);long cents=0;int count=0;char*p=buf;while((p=strstr(p,"\"amount\""))){p=strchr(p,':')+1;while(*p==' '||*p=='"')p++;long dollars=0,part=0;if(sscanf(p,"%ld.%ld",&dollars,&part)!=2)return 2;cents+=dollars*100+part;count++;p++;}fprintf(o,"{\"schemaVersion\":1,\"data\":{\"summary\":{\"count\":%d,\"total\":\"%ld.%02ld\"}},\"artifacts\":[]}",count,cents/100,cents%100);'''
    if mode=='types':action=r'''char buf[65536];FILE*i=fopen(getenv("SLEEP_IN_INPUT_FILE"),"r");size_t n=fread(buf,1,sizeof(buf)-1,i);buf[n]=0;fclose(i);char*start=strchr(buf,':')+1;char*end=strrchr(buf,'}');*end=0;fputs("{\"schemaVersion\":1,\"data\":{\"received\":",o);fputs(start,o);fputs("},\"artifacts\":[]}",o);'''
    return pre+'FILE*o=fopen(getenv("SLEEP_IN_OUTPUT_FILE"),"w");'+action+'return fclose(o);}'

@pytest.mark.parametrize('language',LANGUAGES[1:])
def test_native_fixture_summary_parser(matrix,language,tmp_path):
    store,prepared=matrix
    node=copy.deepcopy(prepared[(language,'producer')]);node['source']=fixture_program(language,'summary');node['config']['entry_mode']='file'
    node.pop('_build',None);node=prepare_node(store,node)
    result=run_script(node,{'orders':ORDERS},tmp_path/'summary',store.path)
    assert result['status']=='succeeded',result
    assert result['output']['data']=={'summary':{'count':3,'total':'30.75'}}


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='BLOCKED_ENV: actual pinned n8n required')
@pytest.mark.parametrize('variant,source_group', [(v,g) for v in ['baseline','order','empty','fail'] for g in [0,1]] + [('types',0),('artifact',0)])
def test_actual_n8n_fixed_pair_design(matrix,monkeypatch,variant,source_group):
    import collections,socket,threading,time,uvicorn
    from fastapi import FastAPI
    from taskconsole.workflows import register_workflow_routes
    import taskconsole.workflows as workflows
    from taskconsole.workflows_n8n import execute_graph
    store,prepared=matrix;app=FastAPI();service=register_workflow_routes(app,store,lambda request,tx,admin=False:{'id':'matrix','role':'admin'})
    if variant=='artifact':
        from taskconsole.app import create_app
        from taskconsole.workflows import WorkflowService
        monkeypatch.delenv('SLEEP_IN_LOCAL',raising=False)
        app=create_app(store.path,'sqlite:///'+str(store.path/'state.sqlite'))
        service=WorkflowService(app.state.store)
    sql_calls=collections.Counter();original=workflows.execute_sql
    def count_sql(store,node,*args,**kwargs):
        sql_calls[node['id']]+=1
        return original(store,node,*args,**kwargs)
    monkeypatch.setattr(workflows,'execute_sql',count_sql)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
    deadline=time.monotonic()+10
    while not server.started and time.monotonic()<deadline:time.sleep(.05)
    assert server.started
    monkeypatch.setenv('SLEEP_IN_BASE_URL',f'http://127.0.0.1:{port}')
    authenticated=None
    if variant=='artifact':
        import httpx
        authenticated=httpx.Client(base_url=f'http://127.0.0.1:{port}')
        response=authenticated.post('/api/setup',json={'token':(store.path/'setup-token').read_text().strip(),'username':'pair_owner','password':'pair acceptance password','timezone':'UTC','locale':'en'})
        assert response.status_code==200,response.text
        authenticated.headers['X-CSRF-Token']=response.json()['csrf']
    sources=LANGUAGES[1:] if variant in {'types','artifact'} else LANGUAGES[:4] if source_group==0 else LANGUAGES[4:]
    targets=LANGUAGES[1:] if variant in {'types','artifact'} else LANGUAGES
    nodes=[];edges=[];expected={}
    def make(language,nid,source):
        node=copy.deepcopy(prepared[(language,'producer')]);node['id']=nid;node['source']=source;node['inputs']={};node.pop('_build',None)
        if language!='sql':node['config']['entry_mode']='file'
        return node
    try:
        if variant=='fail':
            setup=copy.deepcopy(prepared[('sql','producer')]);setup['source']='CREATE TABLE IF NOT EXISTS pair_effects(operation_key TEXT)';setup['config']['mode']='write'
            execute_sql(store,setup,{},'fixture')
        for language in sources:
            sid='source_'+language
            source=fixture_program(language,'fail' if variant=='fail' else 'emit',FTYPES if variant=='types' else {'rows':[] if variant=='empty' else ORDERS}) if language!='sql' else ('SELECT * FROM deliberately_absent_table' if variant=='fail' else 'SELECT order_id,amount,region FROM orders '+('WHERE 1=0' if variant=='empty' else 'ORDER BY order_id'))
            if variant=='artifact':source=fixture_artifact_program(language,'producer')
            nodes.append(make(language,sid,source))
            for target in targets:
                nid=language+'_to_'+target
                if target=='sql':
                    source='SELECT 1 AS ignored' if variant=='order' else 'SELECT order_id,amount,region FROM orders WHERE 1=0' if variant=='empty' else 'SELECT order_id,amount,region FROM orders WHERE order_id IN (:id1,:id2,:id3) ORDER BY order_id'
                else:source=fixture_program(target,'emit' if variant=='order' else 'types' if variant=='types' else 'summary',{'ignored':True} if variant=='order' else None)
                if variant=='artifact':source=fixture_artifact_program(target,'consumer')
                node=make(target,nid,source)
                if variant=='artifact':node['inputs']={'file':{'source':'artifact','node_id':sid,'name':'bytes.bin'}}
                elif variant=='types':node['inputs']={'payload':{'source':'node','node_id':sid,'path':[]}}
                elif variant not in {'order','empty'} and target=='sql':node['inputs']={'id'+str(i+1):{'source':'node','node_id':sid,'path':['rows',i,'order_id']} for i in range(3)}
                elif variant!='order' and target!='sql':node['inputs']={'orders':{'source':'node','node_id':sid,'path':['rows']}}
                if variant=='fail':
                    if target=='sql':
                        node['source']="INSERT INTO pair_effects(operation_key) VALUES('effect-001')";node['config']['mode']='write'
                    else:node['source']=node['source'].replace('business-calls','effect-001')
                nodes.append(node);edges.append({'source':sid,'target':nid});expected[nid]=target
        wf=service.save({'name':'Fixed pair '+variant+str(source_group),'timeout':240,'nodes':nodes,'edges':edges});publication=service.publish(wf['id']);run=service.admit(wf['id'])
        execute_graph(service,run['id']);result=service.get_run(run['id'])
        assert result['status']==('failed' if variant=='fail' else 'succeeded'),(result.get('error'),result.get('adapter_log'))
        assert result.get('n8n_execution_id') and result.get('graph_execution_id')
        assert result['version_id']==publication['version_id']
        for language in sources:
            sid='source_'+language;state=result['nodes'][sid]
            assert len(state['attempts'])==1 and state['status']==('failed' if variant=='fail' else 'succeeded')
            assert (sql_calls[sid] if language=='sql' else (store.path/'workflow-runs'/run['id']/sid/'attempt-1'/'project'/'business-calls').read_text().count('1\n'))==1
            if variant not in {'fail','artifact'}: assert state['output']['data']==FTYPES if variant=='types' else state['output']['data']['rows']==([] if variant=='empty' else ORDERS)
        for nid,target in expected.items():
            state=result['nodes'][nid];counter=store.path/'workflow-runs'/run['id']/nid/'attempt-1'/'project'/'business-calls'
            if variant=='fail':
                assert state['status']=='not_run' and state['reason']=='blocked_by_failed_dependency'
                assert state['attempts']==[] and not counter.exists() and sql_calls[nid]==0
                assert not counter.with_name('effect-001').exists()
                continue
            assert state['status']=='succeeded' and len(state['attempts'])==1
            assert (sql_calls[nid] if target=='sql' else counter.read_text().count('1\n'))==1
            if variant=='order':
                assert state['inputs']=={}
                assert state['output']['data']['rows']==[{'ignored':1}] if target=='sql' else state['output']['data']=={'ignored':True}
            elif variant=='artifact':
                assert state['output']['data']=={'size':4,'hex':'00ff0a41'}
                ref=state['inputs']['file'];assert '/'+nid+'/attempt-1/inputs/' in ref['path']
                assert Path(ref['path']).read_bytes()==bytes([0,255,10,65])
                import hashlib
                assert ref['sha256']==hashlib.sha256(bytes([0,255,10,65])).hexdigest()
            elif variant=='types':
                assert state['inputs']=={'payload':FTYPES} and state['output']['data']=={'received':FTYPES}
            elif target=='sql':
                assert state['inputs']==({} if variant=='empty' else {'id1':'A001','id2':'A002','id3':'A003'})
                assert state['output']['data']['rows']==([] if variant=='empty' else ORDERS)
                assert state['output']['data']['rowCount']==(0 if variant=='empty' else 3)
            else:
                assert state['inputs']=={'orders':[] if variant=='empty' else ORDERS}
                assert state['output']['data']=={'summary':{'count':0 if variant=='empty' else 3,'total':'0.00' if variant=='empty' else '30.75'}}
        if variant=='artifact':
            import httpx
            from taskconsole.workflows_execution import materialize_artifact
            for artifact in result['artifacts']:
                endpoint=f'/api/workflow-runs/{run["id"]}/artifacts/{artifact["id"]}'
                response=authenticated.get(endpoint)
                assert response.status_code==200 and response.content==bytes([0,255,10,65])
                assert httpx.get(f'http://127.0.0.1:{port}'+endpoint).status_code==401
                with pytest.raises(ValueError,match='outside'):
                    materialize_artifact(store,{'id':'other-run','artifacts':[artifact]},{'node_id':artifact['node_id'],'name':'bytes.bin'},store.path/'workflow-runs'/'other-run'/'consumer'/'attempt-1')
            authenticated.close()
        if variant=='fail':
            query=copy.deepcopy(prepared[('sql','producer')]);query['source']='SELECT COUNT(*) AS n FROM pair_effects'
            assert execute_sql(store,query,{},'fixture')['output']['data']['rows']==[{'n':0}]
    finally:server.should_exit=True;thread.join(timeout=10)


def fixture_artifact_program(language, role):
    """Four-byte binary artifact producer/reader in each real language."""
    envelope=json.dumps({'schemaVersion':1,'data':{},'artifacts':[{'name':'bytes.bin'}]})
    if language=='python':
        action='pathlib.Path(os.environ["SLEEP_IN_ARTIFACT_DIR"],"bytes.bin").write_bytes(bytes([0,255,10,65]))\nout='+repr(json.loads(envelope)) if role=='producer' else 'i=json.load(open(os.environ["SLEEP_IN_INPUT_FILE"]))\nb=pathlib.Path(i["file"]["path"]).read_bytes()\nout={"schemaVersion":1,"data":{"size":len(b),"hex":b.hex()},"artifacts":[]}'
        return 'import os,json,pathlib\npathlib.Path("business-calls").open("a").write("1\\n")\n'+action+'\njson.dump(out,open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))'
    if language=='javascript':
        action='fs.writeFileSync(process.env.SLEEP_IN_ARTIFACT_DIR+"/bytes.bin",Buffer.from([0,255,10,65]));const out='+envelope+';' if role=='producer' else 'const i=JSON.parse(fs.readFileSync(process.env.SLEEP_IN_INPUT_FILE,"utf8"));const b=fs.readFileSync(i.file.path);const out={schemaVersion:1,data:{size:b.length,hex:b.toString("hex")},artifacts:[]};'
        return 'const fs=require("fs");fs.appendFileSync("business-calls","1\\n");'+action+'fs.writeFileSync(process.env.SLEEP_IN_OUTPUT_FILE,JSON.stringify(out));'
    if language=='shell':
        if role=='producer':return 'printf "1\\n" >> business-calls\nprintf "\\000\\377\\012\\101" > "$SLEEP_IN_ARTIFACT_DIR/bytes.bin"\ncat > "$SLEEP_IN_OUTPUT_FILE" <<\'EOF_BYTES\'\n'+envelope+'\nEOF_BYTES\n'
        return '''printf "1\\n" >> business-calls
p=$(sed -n 's/.*"path": *"\\([^"]*\\)".*/\\1/p' "$SLEEP_IN_INPUT_FILE")
hex=$(od -An -tx1 "$p" | tr -d ' \\n')
size=$(wc -c < "$p" | tr -d ' ')
printf '{"schemaVersion":1,"data":{"size":%s,"hex":"%s"},"artifacts":[]}' "$size" "$hex" > "$SLEEP_IN_OUTPUT_FILE"
'''
    if language=='java':
        action='Files.write(Path.of(System.getenv("SLEEP_IN_ARTIFACT_DIR"),"bytes.bin"),new byte[]{0,(byte)255,10,65});String out='+json.dumps(envelope)+';' if role=='producer' else r'''String i=Files.readString(Path.of(System.getenv("SLEEP_IN_INPUT_FILE")));java.util.regex.Matcher m=java.util.regex.Pattern.compile("\"path\"\\s*:\\s*\"([^\"]+)\"").matcher(i);if(!m.find())throw new Exception("missing path");byte[]b=Files.readAllBytes(Path.of(m.group(1)));String out="{\"schemaVersion\":1,\"data\":{\"size\":"+b.length+",\"hex\":\""+java.util.HexFormat.of().formatHex(b)+"\"},\"artifacts\":[]}";'''
        return 'import java.nio.file.*;public class Main{public static void main(String[]a)throws Exception{Files.writeString(Path.of("business-calls"),"1\\n",StandardOpenOption.CREATE,StandardOpenOption.APPEND);'+action+'Files.writeString(Path.of(System.getenv("SLEEP_IN_OUTPUT_FILE")),out);}}'
    action='char path[65536];snprintf(path,sizeof(path),"%s/bytes.bin",getenv("SLEEP_IN_ARTIFACT_DIR"));FILE*b=fopen(path,"wb");unsigned char bytes[]={0,255,10,65};fwrite(bytes,1,4,b);fclose(b);fputs('+json.dumps(envelope)+',o);' if role=='producer' else r'''char input[65536];FILE*i=fopen(getenv("SLEEP_IN_INPUT_FILE"),"r");size_t n=fread(input,1,sizeof(input)-1,i);input[n]=0;fclose(i);char*p=strstr(input,"\"path\"");if(!p)return 2;p=strchr(p,':')+1;while(*p==' ')p++;p++;char*end=strchr(p,'"');*end=0;FILE*b=fopen(p,"rb");if(!b)return 3;unsigned char bytes[100];n=fread(bytes,1,100,b);fclose(b);fprintf(o,"{\"schemaVersion\":1,\"data\":{\"size\":%zu,\"hex\":\"",n);for(size_t x=0;x<n;x++)fprintf(o,"%02x",bytes[x]);fputs("\"},\"artifacts\":[]}",o);'''
    return '#include <stdio.h>\n#include <stdlib.h>\n#include <string.h>\nint main(void){FILE*c=fopen("business-calls","a");fputs("1\\n",c);fclose(c);FILE*o=fopen(getenv("SLEEP_IN_OUTPUT_FILE"),"w");'+action+'return fclose(o);}'


@pytest.fixture(scope='module')
def external_pair_connections(matrix):
    """Explicit opt-in disposable tables using the documented CI fixture roles."""
    from test_workflow_external_sql import fixture_config, wait_for_oracle_fixture_ddl
    from taskconsole.workflows_sql import connect, sql_text
    import uuid
    if os.environ.get('SLEEP_IN_EXTERNAL_SQL_TESTS')!='1':
        pytest.skip('BLOCKED_ENV: opt-in real PostgreSQL/MySQL/Oracle fixtures required')
    store,_=matrix
    connections={};cleanup=[]
    try:
        for dialect in ['postgresql','mysql','oracle']:
            public=create_connection(store,{'name':'External pair '+dialect,'dialect':dialect,'config':fixture_config(dialect),'write_enabled':True})
            with store.transaction() as tx:connection=tx.get('connection',public['id'])
            table='si_pair_'+uuid.uuid4().hex[:12]
            db=connect(store,connection,True)
            cleanup.append((db,table))
            text='VARCHAR2(100 CHAR)' if dialect=='oracle' else 'VARCHAR(100)'
            with db.cursor() as cursor:
                cursor.execute(f'CREATE TABLE {table}(order_id VARCHAR(30) PRIMARY KEY, amount {text}, region {text})')
                for row in ORDERS:
                    cursor.execute(sql_text(f'INSERT INTO {table}(order_id,amount,region) VALUES(:order_id,:amount,:region)',dialect),row)
            db.commit()
            if dialect=='oracle':wait_for_oracle_fixture_ddl(db,table)
            connections[dialect]={'connection_id':public['id'],'dialect':dialect,'mode':'query','table':table}
        yield connections
    finally:
        for db,table in reversed(cleanup):
            try:
                db.rollback()
                with db.cursor() as cursor:cursor.execute('DROP TABLE '+table)
                db.commit()
            finally:db.close()


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='BLOCKED_ENV: actual pinned n8n required')
@pytest.mark.parametrize('dialect',['postgresql','mysql','oracle'])
def test_actual_n8n_external_directed_pairs(matrix,external_pair_connections,monkeypatch,dialect):
    """12 script directions plus two cross-database directions per dialect."""
    import collections,socket,threading,time,uvicorn
    from fastapi import FastAPI
    import taskconsole.workflows as workflows
    from taskconsole.workflows import register_workflow_routes
    from taskconsole.workflows_n8n import execute_graph
    store,prepared=matrix;app=FastAPI();service=register_workflow_routes(app,store,lambda request,tx,admin=False:{'id':'external-matrix','role':'admin'})
    calls=collections.Counter();original=workflows.execute_sql
    def count_sql(store,node,*args,**kwargs):
        calls[node['id']]+=1
        return original(store,node,*args,**kwargs)
    monkeypatch.setattr(workflows,'execute_sql',count_sql)
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1];sock.close()
    server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=port,log_level='error'));thread=threading.Thread(target=server.run,daemon=True);thread.start()
    until=time.monotonic()+10
    while not server.started and time.monotonic()<until:time.sleep(.05)
    assert server.started
    monkeypatch.setenv('SLEEP_IN_BASE_URL',f'http://127.0.0.1:{port}')
    def sql_node(nid,db,bound=False):
        config=external_pair_connections[db].copy();table=config.pop('table')
        source=f'SELECT order_id AS "order_id",amount AS "amount",region AS "region" FROM {table}'
        if bound:source+=' WHERE order_id IN (:id1,:id2,:id3)'
        return {'id':nid,'kind':'sql','source':source+' ORDER BY order_id','config':config,'inputs':{}}
    def script_node(nid,language,mode):
        node=copy.deepcopy(prepared[(language,'producer')]);node.update(id=nid,source=fixture_program(language,mode,{'rows':ORDERS} if mode=='emit' else None),inputs={});node.pop('_build',None);node['config']['entry_mode']='file'
        return node
    nodes=[sql_node('database_source',dialect)];edges=[];expected={}
    for language in LANGUAGES[1:]:
        to_script=script_node('to_'+language,language,'summary')
        to_script['inputs']={'orders':{'source':'node','node_id':'database_source','path':['rows']}}
        native=script_node('from_'+language,language,'emit')
        to_db=sql_node(language+'_to_database',dialect,True)
        to_db['inputs']={'id'+str(i+1):{'source':'node','node_id':native['id'],'path':['rows',i,'order_id']} for i in range(3)}
        nodes += [to_script,native,to_db]
        edges += [{'source':'database_source','target':to_script['id']},{'source':native['id'],'target':to_db['id']}]
        expected[to_script['id']]='summary';expected[to_db['id']]='bound'
    for other in external_pair_connections:
        if other==dialect:continue
        target=sql_node('database_to_'+other,other,True)
        target['inputs']={'id'+str(i+1):{'source':'node','node_id':'database_source','path':['rows',i,'order_id']} for i in range(3)}
        nodes.append(target);edges.append({'source':'database_source','target':target['id']});expected[target['id']]='bound'
    try:
        wf=service.save({'name':'Real external pairs '+dialect,'timeout':240,'nodes':nodes,'edges':edges});publication=service.publish(wf['id']);run=service.admit(wf['id'])
        execute_graph(service,run['id']);result=service.get_run(run['id'])
        assert result['status']=='succeeded',(result.get('error'),result.get('adapter_log'))
        assert result.get('n8n_execution_id') and result.get('graph_execution_id')
        assert result['version_id']==publication['version_id']
        for node in nodes:
            state=result['nodes'][node['id']]
            assert state['status']=='succeeded' and len(state['attempts'])==1
            counter=store.path/'workflow-runs'/run['id']/node['id']/'attempt-1'/'project'/'business-calls'
            assert (calls[node['id']] if node['kind']=='sql' else counter.read_text().count('1\n'))==1
            if expected.get(node['id'])=='summary':
                assert state['inputs']=={'orders':ORDERS}
                assert state['output']['data']=={'summary':{'count':3,'total':'30.75'}}
            elif node['kind']=='sql':
                assert state['inputs']==({'id1':'A001','id2':'A002','id3':'A003'} if expected.get(node['id'])=='bound' else {})
                assert state['output']['data']['rows']==ORDERS and state['output']['data']['rowCount']==3
            else:assert state['output']['data']=={'rows':ORDERS}
    finally:server.should_exit=True;thread.join(timeout=10)
