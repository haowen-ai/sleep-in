"""Real native n8n and local synthetic effects; no external endpoints."""
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
import httpx
from test_workflow_execution_n8n_complete import live
from test_workflows import service, simple_graph
from taskconsole.workflows import public_run
from taskconsole.workflows_n8n import execute_graph
from test_acceptance_schedule_security import utc, rows


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'), reason='actual n8n command required')
def test_committed_http_write_lost_ack_is_not_replayed_and_requires_review(live, tmp_path):
    ledger = tmp_path / 'receiver-effects.jsonl'
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers['Content-Length']))
            with ledger.open('a') as stream:
                stream.write(json.dumps({'path': self.path, 'body': body.decode()}) + '\n')
                stream.flush()
                os.fsync(stream.fileno())
            self.connection.shutdown(socket.SHUT_RDWR)
            self.connection.close()
        def log_message(self, *args):
            pass
    receiver = ThreadingHTTPServer(('127.0.0.1', 0), Receiver)
    thread = threading.Thread(target=receiver.serve_forever, daemon=True)
    thread.start()
    try:
        url = 'http://127.0.0.1:' + str(receiver.server_port) + '/effect-001'
        source = 'import urllib.request\ndef main(inputs):\n urllib.request.urlopen(urllib.request.Request(' + repr(url) + ',data=b"effect-001",method="POST"),timeout=5).read()\n return {"ack":True}'
        graph = simple_graph()
        graph['nodes'][0]['source'] = source
        graph['nodes'][1]['source'] = 'def main(inputs): raise RuntimeError("blocked consumer must not execute")'
        wf = live.save(graph)
        live.publish(wf['id'])
        run = live.admit(wf['id'], key='lost-http-ack')
        execute_graph(live, run['id'])
        response = httpx.get(os.environ['SLEEP_IN_BASE_URL'] + '/api/workflow-runs/' + run['id'])
        assert response.status_code == 200
        result = response.json()
        assert result['status'] == 'failed'
        assert result['nodes']['a']['status'] == 'failed'
        assert len(result['nodes']['a']['attempts']) == 1
        assert result['nodes']['b']['attempts'] == []
        assert result['nodes']['b']['status'] == 'not_run'
        assert [json.loads(line) for line in ledger.read_text().splitlines()] == [{'path': '/effect-001', 'body': 'effect-001'}]
        assert result['side_effects_uncertain'] is True
        assert result['review_reason']['code'] == 'check_external_effects'
        assert 'before' in result['review_reason']['message'].lower()
        # Re-reading, repeat orchestration, and the same admission key cannot rerun business work.
        execute_graph(live, run['id'])
        assert live.admit(wf['id'], key='lost-http-ack')['id'] == run['id']
        assert len(ledger.read_text().splitlines()) == 1
        assert len(rows(live, 'workflow_run')) == 1
    finally:
        receiver.shutdown()
        receiver.server_close()
        thread.join(timeout=5)


def test_rejected_inputs_and_successful_runs_do_not_claim_uncertain_effects(tmp_path):
    svc = service(tmp_path)
    graph = simple_graph()
    graph['nodes'][0]['inputs'] = {'missing': {'source': 'parameter', 'path': 'not_supplied'}}
    wf = svc.save(graph)
    svc.publish(wf['id'])
    run = svc.admit(wf['id'])
    svc.execute_node(run['id'], 'a')
    svc.execute_node(run['id'], 'b')
    rejected = public_run(svc.finish(run['id']))
    assert rejected['nodes']['a']['attempts'] == []
    assert rejected['side_effects_uncertain'] is False
    assert rejected['review_reason'] is None
    good = svc.save(simple_graph())
    svc.publish(good['id'])
    success = svc.admit(good['id'])
    for nid in ('a', 'b'):
        svc.execute_node(success['id'], nid)
    result = public_run(svc.finish(success['id']))
    assert result['status'] == 'succeeded'
    assert result['side_effects_uncertain'] is False
    assert result['review_reason'] is None


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_N8N_COMMAND'), reason='actual n8n command required')
def test_sch023_actual_ninety_second_worker_prevents_parallel_write(live, tmp_path):
    ledger = tmp_path / 'ninety-second-effect.json'
    source = 'import time,json\nfrom pathlib import Path\ndef main(inputs):\n p=Path(' + repr(str(ledger)) + ')\n started=time.monotonic()\n p.write_text(json.dumps({"started":started,"writes":1}))\n time.sleep(90)\n p.write_text(json.dumps({"started":started,"finished":time.monotonic(),"writes":1}))\n return {"effect":"once"}'
    graph = {'name': 'Ninety second overlap', 'timeout': 150,
        'nodes': [{'id': 'slow', 'kind': 'python', 'source': source, 'config': {'timeout': 120}, 'inputs': {}}], 'edges': []}
    wf = live.save(graph)
    live.publish(wf['id'])
    wf['triggers'] = [{'id': 'clock', 'kind': 'scheduled', 'enabled': True,
        'schedule': {'kind': 'interval', 'every': 1, 'anchor': '2026-09-21T07:00Z'}, 'timezone': 'UTC'}]
    live.save(wf, wf['id'])
    live.tick(utc('2026-09-21T06:59:59Z'))
    run = live.tick(utc('2026-09-21T07:00Z'))[0]
    worker = threading.Thread(target=execute_graph, args=(live, run['id']))
    worker.start()
    try:
        deadline = time.monotonic() + 35
        while not ledger.exists() and time.monotonic() < deadline:
            time.sleep(.05)
        assert ledger.exists()
        started = json.loads(ledger.read_text())['started']
        # Let a real minute elapse with the native worker alive before the next tick.
        while time.monotonic() - started < 60:
            time.sleep(.2)
        assert worker.is_alive()
        assert live.get_run(run['id'])['nodes']['slow']['status'] == 'running'
        live.tick(utc('2026-09-21T07:00:59Z'))
        assert live.tick(utc('2026-09-21T07:01Z')) == []
        assert len(rows(live, 'workflow_run')) == 1
        skipped = [row for row in rows(live, 'workflow_occurrence') if row['status'] == 'skipped']
        assert len(skipped) == 1 and skipped[0]['reason'] == 'overlap_skipped'
        assert skipped[0]['occurrence'] == utc('2026-09-21T07:01Z').isoformat()
        worker.join(timeout=50)
        assert not worker.is_alive()
        result = live.get_run(run['id'])
        assert result['status'] == 'succeeded', result.get('error')
        effect = json.loads(ledger.read_text())
        assert effect['finished'] - effect['started'] >= 90 and effect['writes'] == 1
        assert len(result['nodes']['slow']['attempts']) == 1
    finally:
        if worker.is_alive():
            live.cancel(run['id'])
            worker.join(timeout=10)
