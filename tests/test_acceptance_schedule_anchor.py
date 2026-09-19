"""A v2 interval must keep one explicit phase across preview and dispatch."""
from datetime import datetime
import pytest

from taskconsole.schedule import workflow_next_runs
from test_workflow_api_contract import client, admin, saved


@pytest.mark.parametrize('anchor',[None,''])
def test_interval_without_anchor_cannot_preview_enable_or_publish(client,anchor):
    admin(client);wf=saved(client);prefix='/api/workflows/'+wf['id']
    assert client.post(prefix+'/publish').status_code==200
    spec={'kind':'interval','every':15,'unit':'minutes'}
    if anchor is not None:spec['anchor']=anchor
    before=client.get(prefix).json()
    preview=client.post(prefix+'/preview',json={'schedule':spec,'timezone':'UTC','after':'2026-09-21T08:07Z'})
    assert preview.status_code==400,preview.text
    assert 'anchor' in preview.json()['detail']['message'].lower()
    update=client.put(prefix,json={**before,'schedule':spec,'enabled':True})
    assert update.status_code==400,update.text
    assert client.get(prefix).json()==before
    # Incomplete disabled drafts remain editable, with publication blocked.
    draft=client.put(prefix,json={**before,'schedule':spec})
    assert draft.status_code==200
    assert any('anchor' in e['message'].lower() for e in draft.json()['validation_errors'])
    assert client.post(prefix+'/publish').status_code==422
    assert client.get(prefix).json()['published_version_id']==before['published_version_id']
    with pytest.raises(ValueError,match='[Aa]nchor'):
        workflow_next_runs(spec,'UTC',datetime.fromisoformat('2026-09-21T08:07+00:00'))


def test_explicit_interval_phase_does_not_rebase_when_preview_advances():
    spec={'kind':'interval','every':15,'anchor':'2026-09-21T08:00Z'}
    for minute in (7,8,14):
        result=workflow_next_runs(spec,'UTC',datetime.fromisoformat(f'2026-09-21T08:{minute:02}:00+00:00'))
        assert result[0].isoformat()=='2026-09-21T08:15:00+00:00'
