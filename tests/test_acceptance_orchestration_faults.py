"""Real n8n transport/import faults cannot fabricate completion or replay effects."""
import socket
import time

import pytest
from test_acceptance_data import live, configured_node
from test_acceptance_branches import native, admit, calls, requires_n8n
import taskconsole.workflows_n8n as adapter


@requires_n8n
def test_actual_n8n_exit_zero_without_finish_is_failed_with_effect_preserved(live,monkeypatch):
    run=admit(live,[native('A',{'effect':1})],[],'Missing terminal callback')
    original=adapter.compile_graph
    def without_finish(*args):
        graph=original(*args)
        finish=next(n for n in graph['nodes'] if n['name']=='finish')
        finish.update(type='n8n-nodes-base.noOp',typeVersion=1,parameters={})
        return graph
    monkeypatch.setattr(adapter,'compile_graph',without_finish)
    adapter.execute_graph(live,run['id'])
    result=live.get_run(run['id'])
    assert result['status']=='failed'
    assert 'without an authenticated terminal callback' in result['error']
    assert result['n8n_execution_id'] and result['graph_execution_id']
    assert result['adapter_finished_at'] and result['adapter_log']
    assert run['callback_token'] not in result['adapter_log']
    assert result['nodes']['A']['output']['data']=={'effect':1}
    calls(live,result,'A',1)
    adapter.execute_graph(live,run['id'])
    assert live.get_run(run['id'])==result
    calls(live,result,'A',1)


@requires_n8n
def test_actual_n8n_import_failure_is_terminal_and_has_diagnostics(live,monkeypatch):
    run=admit(live,[native('A',{'effect':1})],[],'Rejected graph import')
    original=adapter.compile_graph
    def malformed(*args):
        graph=original(*args);graph['nodes']='invalid graph node array';return graph
    monkeypatch.setattr(adapter,'compile_graph',malformed)
    started=time.monotonic();adapter.execute_graph(live,run['id'])
    result=live.get_run(run['id'])
    assert time.monotonic()-started<60
    assert result['status']=='failed' and 'n8n command exited' in result['error']
    assert result['adapter_finished_at'] and result['adapter_log']
    assert 'import' in result['adapter_log'].lower()
    assert result['nodes']['A']['status']=='not_run'
    calls(live,result,'A',0)


@requires_n8n
def test_actual_n8n_worker_transport_outage_fails_without_business_invocation(live,monkeypatch):
    run=admit(live,[native('A',{'effect':1})],[],'Worker transport outage')
    original=adapter.compile_graph
    # Own a bound, non-listening loopback port so another process cannot reuse it.
    with socket.socket() as unavailable:
        unavailable.bind(('127.0.0.1',0));port=unavailable.getsockname()[1]
        def disconnected(*args):
            graph=original(*args)
            for node in graph['nodes']:
                params=node.get('parameters',{})
                if '/nodes/' in params.get('url',''):
                    params['url']='http://127.0.0.1:'+str(port)+'/unavailable'
            return graph
        monkeypatch.setattr(adapter,'compile_graph',disconnected)
        started=time.monotonic();adapter.execute_graph(live,run['id'])
    result=live.get_run(run['id'])
    assert time.monotonic()-started<60
    assert result['status']=='failed' and result['adapter_log']
    assert result['n8n_execution_id'] and result['graph_execution_id']
    assert result['nodes']['A']['status']=='not_run'
    assert any(word in result['adapter_log'].lower() for word in ['refused','connect','offline'])
    calls(live,result,'A',0)


@requires_n8n
def test_capabilities_pending_nodes_and_terminal_replay_are_snapshot_scoped(live):
    first=admit(live,[native('A',{'marker':'first'})],[],'First capability')
    second=admit(live,[native('B',{'marker':'second'})],[],'Second capability')
    client=live.acceptance_client
    headers={'x-workflow-token':first['callback_token']}
    first_prefix='/internal/workflows/'+first['id']
    second_prefix='/internal/workflows/'+second['id']
    for path in ['/claim','/nodes/B/submit','/finish']:
        response=client.post(second_prefix+path,json={'execution_id':'wrong'},headers=headers)
        assert response.status_code==403,response.text
    response=client.post(first_prefix+'/nodes/B/submit',json={},headers=headers)
    assert response.status_code in {404,422}
    response=client.post(first_prefix+'/finish',json={},headers=headers)
    assert response.status_code==409,response.text
    assert response.json()['detail']['code']=='dependencies_pending'
    assert response.json()['detail']['pending']==['A']
    assert live.get_run(first['id'])==first and live.get_run(second['id'])==second
    adapter.execute_graph(live,first['id']);completed=live.get_run(first['id'])
    assert completed['status']=='succeeded' and completed['n8n_execution_id']
    # Old capability may read an idempotent terminal result but never change code/version.
    for path in ['/nodes/A/submit','/nodes/A','/finish']:
        response=client.post(first_prefix+path,json={'source':'raise Exception("injected")','version_id':'other','run_id':second['id']},headers=headers)
        assert response.status_code in {200,202},response.text
    assert live.get_run(first['id'])==completed
    calls(live,completed,'A',1)
    assert live.get_run(second['id'])==second
    calls(live,second,'B',0)
