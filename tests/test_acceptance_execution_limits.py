"""Deterministic output failures never repeat business work; log tails are explicit."""
import os
from pathlib import Path
import pytest

from taskconsole.workflows_runtime import run_script
from test_workflow_execution_n8n_complete import live


def test_native_log_tail_is_bounded_marked_and_does_not_truncate_output(tmp_path):
    node={'id':'logs','kind':'python','inputs':{},'config':{},'source':
          'import sys\ndef main(inputs):\n print("x"*250000+"LOG-END")\n print("e"*250000+"ERR-END",file=sys.stderr)\n return {"rows":["preserved"]*20000}'}
    result=run_script(node,{},tmp_path/'worker',tmp_path)
    assert result['status']=='succeeded'
    for name,suffix in [('stdout','LOG-END'),('stderr','ERR-END')]:
        assert len(result[name].encode())<=100200
        assert 'truncated' in result[name].lower()
        assert result[name].endswith(suffix+'\n')
    assert result['output']['data']=={'rows':['preserved']*20000}


def test_native_verbose_worker_keeps_both_log_files_bounded(tmp_path):
    node={'id':'verbose','kind':'python','inputs':{},'config':{},'source':
          'import sys\ndef main(inputs):\n for _ in range(6):\n  print("x"*1048576)\n  print("e"*1048576,file=sys.stderr)\n print("FINAL-OUT")\n print("FINAL-ERR",file=sys.stderr)\n return {"complete":True}'}
    directory=tmp_path/'worker';result=run_script(node,{},directory,tmp_path)
    assert result['status']=='succeeded' and result['output']['data']=={'complete':True}
    for name,suffix in [('stdout','FINAL-OUT'),('stderr','FINAL-ERR')]:
        assert (directory/(name+'.txt')).stat().st_size<=1048576
        assert 'truncated' in result[name].lower() and result[name].endswith(suffix+'\n')
        assert result['log_capture'][name]['bytes_seen']>6*1048576
        assert result['log_capture'][name]['truncated'] is True


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'),reason='Actual pinned n8n required')
@pytest.mark.parametrize('failure',['schema','malformed_envelope'])
def test_actual_n8n_deterministic_output_failure_never_retries_business(live,tmp_path,failure):
    from taskconsole.workflows_n8n import execute_graph
    counter=tmp_path/'business-calls';downstream=tmp_path/'downstream-calls'
    source='from pathlib import Path\nimport os,json\ndef main(inputs):\n'
    source+=f' with Path({str(counter)!r}).open("a") as f:f.write("called\\n")\n'
    source+=' print("saved diagnostics")\n return {"value":"wrong-type"}\n'
    config={'retry':{'max_attempts':3,'safe_to_retry':True,'delay_seconds':0}}
    outputs={'type':'object','properties':{'value':{'type':'integer'}},'required':['value']}
    if failure=='malformed_envelope':
        source=f'from pathlib import Path\nimport os\nwith Path({str(counter)!r}).open("a") as f:f.write("called\\n")\nprint("saved diagnostics")\nPath(os.environ["SLEEP_IN_OUTPUT_FILE"]).write_text("broken json")\n'
        config['entry_mode']='file';outputs={}
    graph={'name':'Permanent output rejection','nodes':[
        {'id':'a','kind':'python','source':source,'inputs':{},'config':config,'outputs':outputs},
        {'id':'b','kind':'python','source':f'from pathlib import Path\ndef main(inputs):\n Path({str(downstream)!r}).write_text("bad")\n return {{}}','inputs':{},'config':{}}],
        'edges':[{'source':'a','target':'b'}]}
    wf=live.save(graph);live.publish(wf['id']);run=live.admit(wf['id']);execute_graph(live,run['id']);result=live.get_run(run['id'])
    assert result['status']=='failed' and result.get('n8n_execution_id')
    assert counter.read_text().splitlines()==['called']
    assert not downstream.exists()
    state=result['nodes']['a'];assert len(state['attempts'])==1
    assert state['retryable'] is False and state['reason']=='invalid_output'
    assert 'saved diagnostics' in state['stdout']
    assert not live.node_status(run['id'],'a')['retry_ready']
