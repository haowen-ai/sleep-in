"""The user's workflow deadline also bounds a stalled engine import."""
import json
import sys
import time

from taskconsole.store import Store
from taskconsole.workflows import WorkflowService
from taskconsole.workflows_n8n import execute_graph


def test_workflow_deadline_stops_real_stalled_import_before_any_node(tmp_path,monkeypatch):
    marker=tmp_path/'engine-started'
    script=tmp_path/'stalled-engine.py'
    script.write_text('import time\nfrom pathlib import Path\nPath('+repr(str(marker))+').touch()\ntime.sleep(8)\n')
    monkeypatch.setenv('SLEEP_IN_N8N_COMMAND',json.dumps([sys.executable,str(script)]))
    store=Store(tmp_path/'state','sqlite:///'+str(tmp_path/'state.sqlite'))
    service=WorkflowService(store)
    wf=service.save({'name':'Bound startup','timeout':1,'nodes':[{'id':'a','kind':'python','source':'def main(inputs): return {}','inputs':{},'config':{}}],'edges':[]})
    service.publish(wf['id']);run=service.admit(wf['id'])
    started=time.monotonic();execute_graph(service,run['id']);elapsed=time.monotonic()-started
    result=service.get_run(run['id'])
    assert marker.exists()
    assert elapsed<4,'A workflow deadline must not add an unadvertised 30-second startup allowance'
    assert result['status']=='timed_out'
    assert result['nodes']['a']['status']=='not_run' and result['nodes']['a']['reason']=='workflow_timed_out'
    assert result['nodes']['a']['attempts']==[] and result['adapter_pid'] is None and result['adapter_finished_at']
    store.engine.dispose()
