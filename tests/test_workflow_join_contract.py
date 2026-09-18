"""A branch skip is ordinary routing; a failed required dependency is not."""
import pytest
from taskconsole.store import Store
from taskconsole.workflows import WorkflowService


def fixture_graph(left='succeeded', right='succeeded', required=True, join='all'):
    def branch(key, outcome):
        source = 'def main(inputs): raise ValueError("expected failure")' if outcome == 'failed' else 'def main(inputs): return {"value": ' + repr(key) + '}'
        return {'id': key, 'kind': 'python', 'name': key, 'source': source, 'config': {}, 'inputs': {}}
    return {'name': 'Join truth table', 'nodes': [
        {'id': 'start', 'kind': 'python', 'name': 'Start', 'source': 'def main(inputs): return {"go": True}', 'inputs': {}, 'config': {}},
        branch('left', left), branch('right', right),
        {'id': 'join', 'kind': 'python', 'name': 'Join', 'source': 'def main(inputs): return inputs',
         'config': {'join': join}, 'inputs': {
             key: {'source': 'node', 'node_id': key, 'path': ['value'], 'optional': True, 'default': None}
             for key in ['left', 'right']}}],
        'edges': [
            {'source': 'start', 'target': key, 'condition': {'path': ['go'], 'operator': 'eq', 'value': state != 'skipped'}}
            for key, state in [('left', left), ('right', right)]] + [
            {'source': 'left', 'target': 'join', 'required': required},
            {'source': 'right', 'target': 'join', 'required': required}]}


@pytest.mark.parametrize('join', ['all', 'any'])
@pytest.mark.parametrize('left,right,required,node_status,run_status,inputs', [
    ('succeeded', 'succeeded', True, 'succeeded', 'succeeded', {'left': 'left', 'right': 'right'}),
    ('skipped', 'succeeded', True, 'succeeded', 'succeeded', {'left': None, 'right': 'right'}),
    ('skipped', 'skipped', True, 'skipped', 'succeeded', None),
    ('failed', 'succeeded', True, 'not_run', 'failed', None),
    ('failed', 'succeeded', False, 'succeeded', 'partial', {'left': None, 'right': 'right'}),
    ('failed', 'failed', False, 'not_run', 'failed', None),
])
def test_join_truth_table(tmp_path, join, left, right, required, node_status, run_status, inputs):
    svc = WorkflowService(Store(tmp_path, 'sqlite:///' + str(tmp_path / 'joins.sqlite')))
    graph = fixture_graph(left, right, required, join)
    wf = svc.save(graph); svc.publish(wf['id']); run = svc.admit(wf['id'])
    for node in graph['nodes']:
        svc.execute_node(run['id'], node['id'])
    result = svc.finish(run['id'])
    assert result['nodes']['join']['status'] == node_status
    assert result['status'] == run_status
    if inputs is not None:
        assert result['nodes']['join']['inputs'] == inputs
        assert result['nodes']['join']['output']['data'] == inputs


def test_any_join_waits_for_all_terminal_markers(tmp_path):
    svc = WorkflowService(Store(tmp_path, 'sqlite:///' + str(tmp_path / 'wait.sqlite')))
    wf = svc.save(fixture_graph(join='any')); svc.publish(wf['id']); run = svc.admit(wf['id'])
    svc.execute_node(run['id'], 'start'); svc.execute_node(run['id'], 'left')
    with pytest.raises(ValueError, match='terminal'):
        svc.execute_node(run['id'], 'join')
    assert svc.get_run(run['id'])['nodes']['join']['status'] == 'queued'
    svc.execute_node(run['id'], 'right'); svc.execute_node(run['id'], 'join')
    assert svc.finish(run['id'])['status'] == 'succeeded'
