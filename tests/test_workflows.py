import copy
from datetime import datetime, timezone
import pytest
from taskconsole.store import Store


def service(tmp_path):
    from taskconsole.workflows import WorkflowService
    return WorkflowService(Store(tmp_path, 'sqlite:///' + str(tmp_path / 'test.sqlite')))


def test_template_executes_complete_rows_once(tmp_path):
    svc = service(tmp_path)
    wf = svc.save(svc.templates()[0])
    publication = svc.publish(wf['id'])
    run = svc.admit(wf['id'], {}, key='once')
    assert svc.admit(wf['id'], {}, key='once')['id'] == run['id']
    for node_id in ('orders', 'summary', 'report'):
        svc.execute_node(run['id'], node_id)
    finished = svc.finish(run['id'])
    assert finished['status'] == 'succeeded'
    assert finished['nodes']['summary']['output']['data']['summary']['count'] == 3
    assert len(finished['artifacts']) == 1
    wf['nodes'][1]['source'] = 'def main(inputs): return {"changed": True}'
    svc.save(wf, wf['id'])
    assert svc.get_run(run['id'])['snapshot']['nodes'][1]['source'] != wf['nodes'][1]['source']
    assert publication['version_id'] == run['version_id']


def test_invalid_cycle_and_mapping_block_publication(tmp_path):
    svc = service(tmp_path)
    wf = svc.templates()[0]
    wf['edges'].append({'source':'report','target':'orders'})
    saved = svc.save(wf)
    with pytest.raises(ValueError, match='cycle'):
        svc.publish(saved['id'])
    wf['edges'] = []
    saved = svc.save(wf, saved['id'])
    with pytest.raises(ValueError, match='upstream'):
        svc.publish(saved['id'])


def test_cancel_before_node_prevents_side_effect(tmp_path):
    svc = service(tmp_path)
    wf = svc.save(svc.templates()[0]); svc.publish(wf['id'])
    run = svc.admit(wf['id'], {})
    svc.cancel(run['id'])
    result = svc.execute_node(run['id'], 'orders')
    assert result['status'] == 'cancelled'
    assert svc.finish(run['id'])['status'] == 'cancelled'


def test_form_schedule_month_end_once_dst():
    from taskconsole.schedule import workflow_next_runs
    start = datetime(2026, 2, 1, tzinfo=timezone.utc)
    assert workflow_next_runs({'kind':'monthly','day':'last','time':'07:30'}, 'UTC', start, count=1)[0].day == 28
    assert len(workflow_next_runs({'kind':'once','date':'2026-02-03','time':'07:30'}, 'UTC', start)) == 1
    dates = workflow_next_runs({'kind':'daily','time':'02:30'}, 'America/Chicago',datetime(2026,3,8,tzinfo=timezone.utc),count=1)
    assert dates[0].day == 9
    with pytest.raises(ValueError): workflow_next_runs({'kind':'cron','cron':'* * * * *'}, 'UTC', start)

@pytest.fixture(autouse=True)
def native_node(monkeypatch):
    from pathlib import Path
    candidate=Path('/Users/ec/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node')
    if candidate.exists(): monkeypatch.setenv('SLEEP_IN_NODE',str(candidate))


def simple_graph(inputs=None, output=None):
    return {'name':'Mapping test','nodes':[
      {'id':'a','name':'Source','kind':'python','source':'def main(inputs): return '+repr(output if output is not None else {'x':7}), 'inputs':{},'config':{}},
      {'id':'b','name':'Consumer','kind':'python','source':'def main(inputs): return {"received": inputs}', 'inputs':inputs or {},'config':{}}
    ],'edges':[{'source':'a','target':'b'}]}


def execute_direct(svc,wf,params=None):
    saved=svc.save(wf);svc.publish(saved['id']);run=svc.admit(saved['id'],params or {})
    for node in wf['nodes']: svc.execute_node(run['id'],node['id'])
    return svc.finish(run['id'])


def test_mapping_order_only_and_explicit_field(tmp_path):
    svc=service(tmp_path)
    for mappings,expected in [({},{}),({'v':{'source':'node','node_id':'a','path':['x']}},{'v':7})]:
        result=execute_direct(svc,simple_graph(mappings))
        assert result['status']=='succeeded'
        assert result['nodes']['b']['inputs']==expected
        assert result['nodes']['b']['output']['data']=={'received':expected}


@pytest.mark.parametrize('value,expected',[(None,None),(False,False),(0,0),('', ''),([],[])])
def test_present_values_do_not_use_optional_default(tmp_path,value,expected):
    svc=service(tmp_path)
    graph=simple_graph({'v':{'source':'node','node_id':'a','path':'x','optional':True,'default':9}}, {'x':value})
    assert execute_direct(svc,graph)['nodes']['b']['inputs']=={'v':expected}


def test_missing_optional_uses_default_required_blocks_process(tmp_path):
    svc=service(tmp_path)
    optional={'v':{'source':'node','node_id':'a','path':'missing','optional':True,'default':9}}
    assert execute_direct(svc,simple_graph(optional))['nodes']['b']['inputs']=={'v':9}
    required={'v':{'source':'node','node_id':'a','path':'missing'}}
    result=execute_direct(svc,simple_graph(required))
    assert result['status']=='failed'
    assert result['nodes']['b']['status']=='failed'
    assert result['nodes']['b'].get('process_started') is not True


def test_optional_without_default_rejected_and_params_mapped(tmp_path):
    svc=service(tmp_path)
    wf=svc.save(simple_graph({'v':{'source':'node','node_id':'a','path':'x','optional':True}}))
    with pytest.raises(ValueError,match='default'):svc.publish(wf['id'])
    result=execute_direct(svc,simple_graph({'name':{'source':'parameter','path':['customer','name']}}),{'customer':{'name':'Ada'}})
    assert result['nodes']['b']['inputs']=={'name':'Ada'}


def test_template_exact_summary_and_empty_rows(tmp_path):
    svc=service(tmp_path)
    graph=svc.templates()[0]
    result=execute_direct(svc,graph)
    assert result['nodes']['summary']['output']['data']=={'summary':{'count':3,'total':'30.75'}}
    assert result['nodes']['report']['output']['data']=={'message':'3 orders • 30.75'}
    graph['nodes'][0]['source']='SELECT order_id,amount,region FROM orders WHERE 1=0'
    result=execute_direct(svc,graph)
    assert result['nodes']['summary']['output']['data']=={'summary':{'count':0,'total':'0.00'}}


def test_helper_schema_version_is_business_data_and_file_mode_envelope(tmp_path):
    from taskconsole.workflows_runtime import run_script
    for language,source in [('python','def main(inputs): return {"schemaVersion":42,"data":{"x":7}}'),('javascript','function main(inputs){return {schemaVersion:42,data:{x:7}}}')]:
        result=run_script({'id':'a','kind':language,'source':source,'config':{}},{},tmp_path/language,tmp_path)
        assert result['output']=={'schemaVersion':1,'data':{'schemaVersion':42,'data':{'x':7}},'artifacts':[]}
    source='import os,json\njson.dump({"schemaVersion":1,"data":{"x":7},"artifacts":[]},open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))'
    result=run_script({'id':'a','kind':'python','source':source,'config':{'entry_mode':'file'}},{},tmp_path/'file',tmp_path)
    assert result['status']=='succeeded' and result['output']['data']=={'x':7}


def test_token_paths_nullable_and_unsupported_schema():
    from taskconsole.workflows_runtime import resolve_path,check_schema
    assert resolve_path({'a.b':[{'x':7}]},['a.b',0,'x'])==7
    with pytest.raises((ValueError,KeyError,IndexError)):resolve_path({'rows':[1]},['rows',-1])
    check_schema(None,{'type':['string','null']})
    with pytest.raises(ValueError):check_schema(7,{'type':['string','null']})
    with pytest.raises(ValueError,match='Unsupported'):check_schema({}, {'type':'object','additionalProperties':False})


def test_failed_required_order_edge_blocks_constant_consumer(tmp_path):
    svc=service(tmp_path);graph=simple_graph({'v':{'source':'constant','value':9}})
    graph['nodes'][0]['source']='def main(inputs): raise ValueError("boom")'
    result=execute_direct(svc,graph)
    assert result['status']=='failed'
    assert result['nodes']['b']['status']=='not_run'
    assert result['nodes']['b']['reason']=='blocked_by_failed_dependency'


def test_schedule_admission_dedupes_named_triggers(tmp_path):
    svc=service(tmp_path);graph=simple_graph()
    graph['triggers']=[{'id':'one','name':'Once','kind':'scheduled','enabled':False,'schedule':{'kind':'once','date':'2026-09-17','time':'07:30'},'timezone':'UTC','params':{}}]
    graph['enabled']=False
    wf=svc.save(graph);svc.publish(wf['id'])
    graph['triggers'][0]['enabled']=True;svc.save(graph,wf['id'])
    moment=datetime(2026,9,17,7,29,59,tzinfo=timezone.utc)
    svc.tick(moment);svc.tick(moment.replace(second=0,minute=30));svc.tick(moment.replace(second=1,minute=30))
    with svc.store.transaction() as tx:runs=tx.all('workflow_run')
    assert len(runs)==1 and runs[0]['trigger_id']=='one'


def test_draft_admission_does_not_nest_store_transactions(tmp_path,monkeypatch):
    from contextlib import contextmanager
    svc=service(tmp_path);wf=svc.save(svc.templates()[0]);original=svc.store.transaction;depth=0
    @contextmanager
    def single():
        nonlocal depth
        assert depth==0,'nested store transaction during admission'
        depth+=1
        try:
            with original() as tx:yield tx
        finally:depth-=1
    monkeypatch.setattr(svc.store,'transaction',single)
    assert svc.admit(wf['id'],{},test=True)['test'] is True


def test_overlap_rejected_after_idempotency_lookup(tmp_path):
    svc=service(tmp_path);wf=svc.save(simple_graph());svc.publish(wf['id'])
    first=svc.admit(wf['id'],{},key='one')
    assert svc.admit(wf['id'],{},key='one')['id']==first['id']
    with pytest.raises(ValueError,match='active'):svc.admit(wf['id'],{},key='two')


@pytest.mark.parametrize('language',['shell','java','c','cpp'])
def test_native_languages_roundtrip_real_file_protocol(tmp_path,language):
    from taskconsole.workflows_runtime import run_script,runtimes,build
    profile=next(r for r in runtimes() if r['language']==language)
    if profile['status']!='ready':pytest.skip('BLOCKED_ENV: '+str(profile['reason']))
    sources={
      'shell':'''printf '{"schemaVersion":1,"data":{"received":' > "$SLEEP_IN_OUTPUT_FILE"
cat "$SLEEP_IN_INPUT_FILE" >> "$SLEEP_IN_OUTPUT_FILE"
printf '},"artifacts":[]}' >> "$SLEEP_IN_OUTPUT_FILE"
''',
      'java':'''import java.nio.file.*; public class Main { public static void main(String[] args) throws Exception { String input=Files.readString(Path.of(System.getenv("SLEEP_IN_INPUT_FILE"))); Files.writeString(Path.of(System.getenv("SLEEP_IN_OUTPUT_FILE")), "{\\"schemaVersion\\":1,\\"data\\":{\\"received\\":"+input+"},\\"artifacts\\":[]}"); }}''',
      'c':'''#include <stdio.h>
#include <stdlib.h>
int main(void){FILE *i=fopen(getenv("SLEEP_IN_INPUT_FILE"),"r"),*o=fopen(getenv("SLEEP_IN_OUTPUT_FILE"),"w");if(!i||!o)return 1;fputs("{\\"schemaVersion\\":1,\\"data\\":{\\"received\\":",o);int c;while((c=fgetc(i))!=EOF)fputc(c,o);fputs("},\\"artifacts\\":[]}",o);fclose(i);fclose(o);return 0;}'''
    }
    sources['cpp']=sources['c']
    node={'id':'echo','kind':language,'source':sources[language],'config':{}}
    if language in {'java','c','cpp'}:node['_build']=build(node,tmp_path)
    payload={'rows':[{'amount':'9007199254740993.01','region':None}],'text':'中文🙂','flag':False}
    result=run_script(node,payload,tmp_path/'run',tmp_path)
    assert result['status']=='succeeded',result
    assert result['output']['data']=={'received':payload}


def test_published_runtime_executable_remains_pinned(tmp_path,monkeypatch):
    svc=service(tmp_path);wf=svc.save(simple_graph());svc.publish(wf['id']);run=svc.admit(wf['id'])
    monkeypatch.setenv('SLEEP_IN_PYTHON','/not/the/published/interpreter')
    svc.execute_node(run['id'],'a');svc.execute_node(run['id'],'b')
    assert svc.finish(run['id'])['status']=='succeeded'


def test_workflow_deadline_stops_running_process(tmp_path):
    import time
    svc=service(tmp_path);graph=simple_graph();graph['timeout']=1
    graph['nodes'][0]['source']='import time\ndef main(inputs):\n time.sleep(5)\n return {}'
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id'])
    start=time.monotonic();svc.execute_node(run['id'],'a')
    assert time.monotonic()-start<3
    result=svc.get_run(run['id']);assert result['status']=='timed_out'
    assert result['nodes']['a']['status']=='timed_out'


def test_public_run_keeps_node_display_identity_without_sources(tmp_path):
    from taskconsole.workflows import public_run
    svc=service(tmp_path);wf=svc.save(simple_graph());svc.publish(wf['id']);run=svc.admit(wf['id'])
    public=public_run(run)
    assert public['nodes']['a']['name']=='Source'
    assert public['nodes']['a']['kind']=='python'
    assert 'snapshot' not in public and 'callback_token' not in public


def test_known_mapping_type_mismatch_blocks_publication(tmp_path):
    svc=service(tmp_path);graph=simple_graph({'v':{'source':'node','node_id':'a','path':'x','type':'number'}})
    graph['nodes'][0]['outputs']={'type':'object','properties':{'x':{'type':'string'}}}
    wf=svc.save(graph)
    with pytest.raises(ValueError,match='incompatible'):svc.publish(wf['id'])


def test_compiler_identity_invalidates_build_cache(tmp_path,monkeypatch):
    import subprocess
    from taskconsole import workflows_runtime as runtime
    profile=next(r for r in runtime.runtimes() if r['language']=='c')
    if profile['status']!='ready':pytest.skip('BLOCKED_ENV: C compiler')
    node={'id':'c','kind':'c','source':'int main(void){return 0;}','config':{}}
    original=runtime.subprocess.run;version=['compiler-1']
    def call(argv,**kwargs):
        if argv[1:] == ['--version']:return subprocess.CompletedProcess(argv,0,stdout=version[0],stderr='')
        return original(argv,**kwargs)
    monkeypatch.setattr(runtime.subprocess,'run',call)
    one=runtime.build(node,tmp_path);version[0]='compiler-2';two=runtime.build(node,tmp_path)
    assert one['digest']!=two['digest']


def test_enabled_named_triggers_require_publication_and_valid_schedule(tmp_path):
    svc=service(tmp_path);graph=simple_graph()
    graph['triggers']=[{'id':'morning','name':'Morning','kind':'scheduled','enabled':True,'schedule':{'kind':'daily','time':'07:30'},'timezone':'UTC','params':{}}]
    with pytest.raises(ValueError,match='Publish'):svc.save(graph)
    graph['triggers'][0]['enabled']=False;wf=svc.save(graph);svc.publish(wf['id'])
    graph['triggers'][0]['enabled']=True;graph['triggers'][0]['schedule']['time']='7:30'
    with pytest.raises(ValueError,match='HH:MM'):svc.save(graph,wf['id'])


def test_append_merge_concatenates_mapped_arrays_in_declared_order(tmp_path):
    svc=service(tmp_path);graph=simple_graph({'right':{'source':'constant','value':[3]},'left':{'source':'node','node_id':'a','path':'values'}},{'values':[1,2]})
    graph['nodes'][1]['config']={'merge':'append','merge_target':'items'}
    result=execute_direct(svc,graph)
    assert result['nodes']['b']['inputs']=={'items':[3,1,2]}
    assert result['nodes']['b']['output']['data']=={'received':{'items':[3,1,2]}}


def test_execution_environment_ids_match_run_and_node(tmp_path):
    svc=service(tmp_path);graph=simple_graph()
    graph['nodes'][0]['source']='import os\ndef main(inputs): return {"run":os.environ["SLEEP_IN_RUN_ID"],"node":os.environ["SLEEP_IN_NODE_ID"]}'
    wf=svc.save(graph);svc.publish(wf['id']);run=svc.admit(wf['id']);svc.execute_node(run['id'],'a')
    assert svc.get_run(run['id'])['nodes']['a']['output']['data']=={'run':run['id'],'node':'a'}


def test_schedule_due_occurrence_survives_failed_admission(tmp_path,monkeypatch):
    svc=service(tmp_path);wf=svc.save(simple_graph());svc.publish(wf['id'])
    wf['enabled']=True;wf['schedule']={'kind':'daily','time':'07:30'};svc.save(wf,wf['id'])
    before=datetime(2026,9,17,7,29,59,tzinfo=timezone.utc);due=before.replace(minute=30,second=0)
    svc.tick(before);original=svc.admit
    def failed(*args,**kwargs):raise RuntimeError('transient admission failure')
    monkeypatch.setattr(svc,'admit',failed)
    try:svc.tick(due)
    except RuntimeError:pass
    monkeypatch.setattr(svc,'admit',original)
    recovered=svc.tick(due.replace(second=1))
    assert len(recovered)==1 and recovered[0]['idempotency_key'].endswith(due.isoformat())
    assert svc.tick(due.replace(second=2))==[]
