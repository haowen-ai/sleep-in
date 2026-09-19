"""Portable values and exact byte thresholds, native workers and actual n8n."""
import copy
import hashlib
import json
import os
from pathlib import Path

import pytest
from taskconsole.workflows_runtime import check_schema, portable, resolve_path, run_script
from taskconsole.workflows_execution import spill_output
from test_acceptance_data import live, configured_node
from test_acceptance_branches import native, mapping, run_graph, calls, requires_n8n
from test_runtime_language_matrix import FTYPES, fixture_program


def worker(tmp_path, value=None, language='python', source=None, outputs=None, inputs=None):
    if source is None:
        source = 'def main(inputs): return ' + repr(value) if language=='python' else 'function main(inputs){return '+json.dumps(value)+'}'
    return run_script({'id':'value','kind':language,'config':{},'source':source,'outputs':outputs or {}}, inputs or {}, tmp_path/'attempt', tmp_path)


@pytest.mark.parametrize('value,schema', [
    ({'rows':[]}, {'type':'object','required':['rows'],'properties':{'rows':{'type':'array'}}}),
    ({'nil':None}, {'type':'object','required':['nil'],'properties':{'nil':{'type':['string','null']}}}),
])
def test_empty_and_nullable_schema_real_worker_once(tmp_path,value,schema):
    check_schema(value,schema)
    result=worker(tmp_path,source='from pathlib import Path\ndef main(inputs):\n with Path("calls").open("a") as f: f.write("1\\n")\n return '+repr(value),outputs=schema)
    assert result['status']=='succeeded' and result['output']['data']==value
    assert (tmp_path/'attempt'/'project'/'calls').read_text()=='1\n'


def test_required_false_empty_values_and_array_paths_are_whole_once(live):
    values={'zero':0,'flag':False,'text':'','empty':[],'object':{},'rows':[{'id':1},{'id':2}]}
    a=native('source',values);bindings={k:mapping('source',path=[k]) for k in values}
    bindings.update(canonical=mapping('source',path=['rows',1,'id']),shorthand=mapping('source',path='rows.1.id'))
    b=native('consumer',inputs=bindings,body='return inputs')
    wf=live.save({'name':'Whole arrays','nodes':[a,b],'edges':[{'source':'source','target':'consumer'}]});live.publish(wf['id']);run=live.admit(wf['id'])
    assert live.execute_node(run['id'],'source')['status']=='succeeded'
    result=live.execute_node(run['id'],'consumer');assert result['status']=='succeeded'
    assert result['inputs']==result['output']['data']=={**values,'canonical':2,'shorthand':2}
    assert type(result['inputs']['canonical']) is int
    calls(live,live.get_run(run['id']),'consumer',1)
    assert resolve_path(values,['rows',1,'id'])==resolve_path(values,'rows.1.id')==2


@pytest.mark.parametrize('path',['rows.-1.id',['rows',-1,'id'],'rows.2.id',['rows',2,'id']])
def test_negative_and_missing_array_indexes_have_no_python_wraparound(path):
    with pytest.raises((ValueError,IndexError)):
        resolve_path({'rows':[{'id':1},{'id':2}]},path)


def test_out_of_range_required_fails_optional_uses_explicit_default(live):
    a=native('source',{'rows':[{'id':1},{'id':2}]})
    required=native('required',inputs={'v':mapping('source',path='rows.2.id')})
    optional=native('optional',inputs={'v':mapping('source',default=9,path=['rows',2,'id'])},body='return inputs')
    wf=live.save({'name':'Missing index','nodes':[a,required,optional],'edges':[{'source':'source','target':n['id']} for n in [required,optional]]});live.publish(wf['id']);run=live.admit(wf['id'])
    live.execute_node(run['id'],'source');bad=live.execute_node(run['id'],'required');good=live.execute_node(run['id'],'optional')
    assert bad['status']=='failed' and 'Missing required' in bad['error'] and not bad.get('process_started')
    assert good['status']=='succeeded' and good['inputs']=={'v':9}
    assert good['input_provenance']['v']['default_used'] is True


def test_prototype_and_code_text_keys_remain_plain_native_javascript_data(live):
    keys=['__proto__','constructor','globalThis.injected=true']
    value={key:{'marker':key} for key in keys};a=native('source',value)
    source='function main(inputs){if(Object.prototype.marker!==undefined || globalThis.injected!==undefined)throw Error("polluted");for(const key of Object.keys(inputs))if(!Object.hasOwn(inputs,key))throw Error("not own");return inputs;}'
    b={'id':'js','kind':'javascript','source':source,'config':{},'inputs':{key:mapping('source',path=[key]) for key in keys}}
    for key in keys:assert resolve_path(value,[key])=={'marker':key}
    wf=live.save({'name':'Plain keys','nodes':[a,b],'edges':[{'source':'source','target':'js'}]});live.publish(wf['id']);run=live.admit(wf['id'])
    live.execute_node(run['id'],'source');result=live.execute_node(run['id'],'js')
    assert result['status']=='succeeded' and result['output']['data']==value


def test_unsafe_integer_is_rejected_before_native_javascript_invocation(live):
    value=9007199254740993
    with pytest.raises(ValueError,match='schema-marked string'):portable(value)
    a=native('source',{'n':value});b=native('consumer',inputs={'n':mapping('source',path=['n'])})
    b['kind']='javascript';b['source']='function main(inputs){require("fs").appendFileSync("invoked","1");return inputs;}'
    wf=live.save({'name':'Unsafe integer','nodes':[a,b],'edges':[{'source':'source','target':'consumer'}]});live.publish(wf['id']);run=live.admit(wf['id'])
    result=live.execute_node(run['id'],'source');assert result['status']=='failed' and 'schema-marked string' in result['error']
    assert live.execute_node(run['id'],'consumer')['status']=='not_run'
    assert not list((live.store.path/'workflow-runs'/run['id']).rglob('invoked'))


@pytest.mark.parametrize('value',['2026-09-17T07:30:00','2026-02-30T07:30:00+08:00'])
def test_timestamp_logical_contract_rejects_missing_zone_and_invalid_date(tmp_path,value):
    schema={'type':'object','properties':{'when':{'type':'string','metadata':{'logicalType':'timestamp'}}}}
    with pytest.raises(ValueError,match='timezone-qualified ISO'):
        check_schema({'when':value},schema)
    with pytest.raises(ValueError,match='timezone-qualified ISO'):
        worker(tmp_path,{'when':value},outputs=schema)


@pytest.mark.parametrize('language,source',[('python','def main(inputs): return {"native":bytes([0,255])}'),('python','def main(inputs): return {"native":object()}'),('javascript','function main(inputs){return {native:Buffer.from([0,255])}}'),('javascript','class Native { toJSON(){return "opaque"} }; function main(inputs){return {native:new Native()}}')])
def test_helpers_reject_native_objects_before_opaque_conversion(tmp_path,language,source):
    result=worker(tmp_path,language=language,source=source)
    assert result['status']=='failed'
    assert 'JSON or artifact' in result['stderr']


def test_depth100_and_ten_thousand_properties_are_deterministic(tmp_path):
    value={'leaf':'end'}
    for _ in range(100):value={'next':value}
    payload={'nested':value,'wide':{str(i):i for i in range(10000)}}
    portable(payload);check_schema(payload,{'type':'object'})
    result=worker(tmp_path,payload)
    assert result['status']=='succeeded' and result['output']['data']==payload


@pytest.mark.parametrize('size',[1024*1024,1024*1024+1])
def test_exact_inclusive_inline_boundary_spills_complete_data(tmp_path,size):
    value={'text':'x'*(size-len(json.dumps({'text':''}).encode()))}
    result=worker(tmp_path,value);raw=json.dumps(value,ensure_ascii=False,allow_nan=False).encode()
    assert len(raw)==size
    spill_output(result,tmp_path/'attempt');output=result['output']
    if size==1024*1024:
        assert output['data']==value and 'data_ref' not in output and output['artifacts']==[]
    else:
        ref=output['data_ref'];assert output['data']=={} and ref['size']==size
        assert Path(ref['path']).read_bytes()==raw and ref['sha256']==hashlib.sha256(raw).hexdigest()
        assert output['artifacts']==[ref]


@requires_n8n
def test_two_mebibyte_data_reference_preview_download_and_downstream_are_complete(live):
    value={'rows':[{'id':i,'value':'e\u0301🙂中文'} for i in range(201)],'padding':''}
    value['padding']='x'*(2*1024*1024-len(json.dumps(value,ensure_ascii=False).encode()))
    raw=json.dumps(value,ensure_ascii=False).encode();assert len(raw)==2*1024*1024
    a=native('source',body='import json\nreturn '+repr(value))
    b=native('consumer',inputs={'whole':mapping('source')},body='import json,hashlib\nv=inputs["whole"]\nreturn {"size":len(json.dumps(v,ensure_ascii=False).encode()),"sha256":hashlib.sha256(json.dumps(v,ensure_ascii=False).encode()).hexdigest(),"rows":v["rows"]}')
    run=run_graph(live,[a,b],[{'source':'source','target':'consumer'}],'Exact two MiB')
    assert run['status']=='succeeded' and run['nodes']['consumer']['output']['data']=={'size':len(raw),'sha256':hashlib.sha256(raw).hexdigest(),'rows':value['rows']}
    calls(live,run,'source',1);calls(live,run,'consumer',1)
    for offset in (0,100,200):
        response=live.acceptance_client.get(f'/api/workflow-runs/{run["id"]}/nodes/source/output',params={'path':'rows','offset':offset,'limit':100})
        assert response.status_code==200,response.text
        page=response.json();assert page['data']==value['rows'][offset:offset+100] and page['total']==201
        assert page['offset']==offset and page['limit']==100 and page['has_more']==(offset+100<201)
    artifact=next(a for a in run['artifacts'] if a['node_id']=='source')
    response=live.acceptance_client.get(f'/api/workflow-runs/{run["id"]}/artifacts/{artifact["id"]}')
    assert response.status_code==200 and response.content==raw
    assert response.content.decode('utf8').count('e\u0301🙂中文')==201 and b'\xef\xbf\xbd' not in response.content


@requires_n8n
def test_marked_decimal_integer_timestamp_python_javascript_java_chain(live):
    from taskconsole.workflows_packs import RuntimePacks
    from taskconsole.workflows_runtime import executable
    javac=os.environ.get('SLEEP_IN_TEST_JAVAC') or executable('java')
    if not javac:pytest.skip('Actual JDK required')
    value=copy.deepcopy(FTYPES);value['combining']='e\u0301🙂'
    properties={key:{'type':'string','metadata':{'logicalType':kind}} for key,kind in [('decimal','decimal'),('largeInteger','integer'),('timestamp','timestamp')]}
    schema={'type':'object','properties':properties,'required':list(properties)}
    python=native('python',value);python['outputs']=schema
    js={'id':'javascript','kind':'javascript','source':'function main(inputs){require("fs").appendFileSync("business-calls","1\\n");return inputs.payload}','inputs':{'payload':mapping('python')},'config':{},'outputs':schema}
    packs=RuntimePacks(live.store);profile=packs.create({'name':'Native Java portability','language':'java','config':{'executable':javac}});version=packs.build(profile['id']);assert version['status']=='ready',version
    java={'id':'java','kind':'java','source':fixture_program('java','types'),'inputs':{'payload':mapping('javascript')},'config':{'entry_mode':'file','runtime_version_id':version['id']},'outputs':{'type':'object','properties':{'received':schema}}}
    run=run_graph(live,[python,js,java],[{'source':'python','target':'javascript'},{'source':'javascript','target':'java'}],'Marked portable chain')
    assert run['status']=='succeeded',run
    assert run['nodes']['python']['output']['data']==run['nodes']['javascript']['output']['data']==value
    assert run['nodes']['java']['output']['data']=={'received':value}
    calls(live,run,'python',1)
    for nid in ['javascript','java']:
        assert (live.store.path/'workflow-runs'/run['id']/nid/'attempt-1'/'project'/'business-calls').read_text()=='1\n'
        assert len(run['nodes'][nid]['attempts'])==1


@pytest.mark.parametrize('value',['2026-09-17T07:30:00+08:00','2026-09-16T23:30:00Z'])
def test_timestamp_logical_contract_preserves_valid_offset_and_utc(tmp_path,value):
    schema={'type':'object','properties':{'when':{'type':'string','metadata':{'logicalType':'timestamp'}}}}
    check_schema({'when':value},schema)
    assert worker(tmp_path,{'when':value},outputs=schema)['output']['data']=={'when':value}


@pytest.mark.parametrize("source",['function main(inputs){return {native:{toJSON(){return "opaque"}}}}','function main(inputs){const native={};Object.defineProperty(native,"toJSON",{value:()=>"opaque"});return {native}}'])
def test_plain_object_custom_serializer_cannot_hide_nonportable_values(tmp_path,source):
    result=worker(tmp_path,language="javascript",source=source)
    assert result['status']=='failed' and 'JSON or artifact' in result['stderr']
