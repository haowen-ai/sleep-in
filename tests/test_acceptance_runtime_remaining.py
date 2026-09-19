"""Measured real graphs, delayed transport replay, and actual DataFrame rejection."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import httpx
import pytest
from test_acceptance_data import live, configured_node
from test_acceptance_branches import native, mapping, admit, calls, requires_n8n
from test_acceptance_gold_concurrency import trace
from taskconsole.workflows_runtime import run_script
import taskconsole.workflows_n8n as adapter


def test_real_pandas_dataframe_helper_rejects_opaque_output(tmp_path,monkeypatch):
    executable=os.environ.get('SLEEP_IN_TEST_PANDAS_PYTHON',sys.executable)
    probe=subprocess.run([executable,'-c','import pandas;print(pandas.__version__)'],capture_output=True,text=True,timeout=60)
    if probe.returncode:pytest.skip('Actual pandas dependency unavailable in selected Python')
    monkeypatch.setenv('SLEEP_IN_PYTHON',executable)
    node={'id':'frame','kind':'python','config':{},'inputs':{},'source':'import pandas as pd\ndef main(inputs):\n frame=pd.DataFrame({"amount":[10.5,20.25]})\n assert frame.shape==(2,1)\n return {"table":frame}'}
    result=run_script(node,{},tmp_path/'attempt',tmp_path)
    assert result['status']=='failed'
    assert 'Nonportable output; use JSON or artifact instead of DataFrame' in result['stderr']
    assert result.get('output') is None


@requires_n8n
@pytest.mark.parametrize('size',[10,25,50])
@pytest.mark.parametrize('shape',['chain','diamond'])
def test_actual_graph_scale_has_exact_effects_and_measured_resources(live,size,shape,record_property):
    # Catches lost/duplicated nodes, incorrect barrier inputs and runaway owned processes.
    nodes=[];edges=[];expected={};parent_ids={}
    for index in range(size):
        nid=f'n{index:02d}'
        if index==0:parents=[]
        elif shape=='chain':parents=[index-1]
        elif index%3==1:parents=[index-1]
        elif index%3==2:parents=[index-2]
        else:parents=[index-2,index-1]
        ids=[f'n{i:02d}' for i in parents];parent_ids[nid]=ids
        expected[nid]=1+sum(expected[p] for p in ids)
        nodes.append(native(nid,inputs={p:mapping(p,path=['value']) for p in ids},config={'join':'all','merge':'named'} if len(ids)>1 else {},body='return {"value":1+sum(inputs.values())}'))
        edges.extend({'source':p,'target':nid} for p in ids)
    run=admit(live,nodes,edges,f'Measured {shape} {size}');rid=run['id'];stop=threading.Event();samples=[];errors=[]
    def monitor():
        try:
            while not stop.wait(.2):
                state=live.get_run(rid)
                roots={p for p in [state.get('adapter_pid'),*[n.get('worker_pid') for n in state['nodes'].values()]] if p}
                if not roots:continue
                output=subprocess.run(['ps','-axo','pid=,ppid=,rss='],capture_output=True,text=True,timeout=5,check=True).stdout
                table={int(p):(int(parent),int(rss)) for p,parent,rss in (line.split() for line in output.splitlines())}
                selected=roots&table.keys()
                while True:
                    descendants={p for p,(parent,_) in table.items() if parent in selected}
                    if descendants<=selected:break
                    selected|=descendants
                samples.append((time.monotonic(),sum(table[p][1] for p in selected),len(selected)))
        except Exception as exc:errors.append(exc)
    watcher=threading.Thread(target=monitor,daemon=True);watcher.start();started=time.monotonic()
    try:adapter.execute_graph(live,rid)
    finally:stop.set();watcher.join(10)
    elapsed=time.monotonic()-started;result=live.get_run(rid)
    assert not errors and not watcher.is_alive() and samples
    peak_kib=max(s[1] for s in samples);peak_processes=max(s[2] for s in samples)
    record_property('shape',shape);record_property('nodes',size);record_property('elapsed_seconds',round(elapsed,3))
    record_property('sampled_peak_owned_rss_kib',peak_kib);record_property('sampled_peak_owned_processes',peak_processes)
    record_property('resource_scope','adapter and worker PID subtrees; RSS sampled every 0.2s, excludes app/server')
    assert result['status']=='succeeded',(result.get('error'),result.get('adapter_log'))
    assert elapsed<180 and 0<peak_kib<2*1024*1024 and peak_processes<=16
    records=trace(live,result)
    for nid,value in expected.items():
        state=result['nodes'][nid];calls(live,result,nid,1)
        assert state['output']['data']=={'value':value}
        assert state['inputs']=={p:expected[p] for p in parent_ids[nid]}
        assert len(records['node_'+nid])==1
        for parent in parent_ids[nid]:assert result['nodes'][parent]['finished_at']<=state['started_at']
    assert len(result['nodes'])==size


@requires_n8n
def test_timeout_before_worker_then_late_callback_and_duplicate_cli_never_replay(live,monkeypatch):
    # The proxy stalls before forwarding the business submission, not after an effect.
    ledger=live.store.path/'effect-ledger'
    node=native('effect',body=f'with Path({str(ledger)!r}).open("a") as f:f.write("effect-001\\n")\nreturn {{"effect":1}}')
    run=admit(live,[node],[],'Delayed callback replay');rid=run['id'];base=os.environ['SLEEP_IN_BASE_URL']
    entered=threading.Event();release=threading.Event();forwarded=threading.Event();late=[];commands=[]
    class Proxy(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):self.forward()
        def do_POST(self):self.forward()
        def forward(self):
            body=self.rfile.read(int(self.headers.get('content-length',0)))
            delayed=self.path.endswith('/nodes/effect/submit')
            if delayed:entered.set();release.wait(45)
            with httpx.Client(timeout=20) as client:
                response=client.request(self.command,base+self.path,content=body,headers={'content-type':'application/json','x-workflow-token':self.headers.get('x-workflow-token','')})
            if delayed:late.append(response.status_code);forwarded.set()
            try:
                self.send_response(response.status_code);self.send_header('Content-Length',str(len(response.content)));self.send_header('Content-Type','application/json');self.end_headers();self.wfile.write(response.content)
            except (BrokenPipeError,ConnectionResetError):pass
    proxy=ThreadingHTTPServer(('127.0.0.1',0),Proxy);server=threading.Thread(target=proxy.serve_forever,daemon=True);server.start()
    original_popen=subprocess.Popen
    def observe(argv,*args,**kwargs):
        if 'execute' in argv and '--rawOutput' in argv:commands.append((list(argv),dict(kwargs['env']),kwargs['cwd']))
        return original_popen(argv,*args,**kwargs)
    monkeypatch.setattr(adapter.subprocess,'Popen',observe)
    monkeypatch.setenv('SLEEP_IN_BASE_URL',f'http://127.0.0.1:{proxy.server_port}')
    try:
        adapter.execute_graph(live,rid);failed=live.get_run(rid)
        assert entered.is_set() and not release.is_set() and not forwarded.is_set()
        assert failed['status']=='failed' and 'n8n command exited' in failed['error']
        assert any(word in failed['adapter_log'].lower() for word in ['timeout','timed out','aborted'])
        assert not ledger.exists();calls(live,failed,'effect',0)
        release.set();assert forwarded.wait(10)
        assert late and late[0] in {200,202,409,422}
        assert live.get_run(rid)==failed and not ledger.exists()
        assert len(commands)==1
        argv,env,cwd=commands[0]
        duplicate=subprocess.run(argv,env=env,cwd=cwd,capture_output=True,text=True,timeout=45)
        assert duplicate.returncode!=0
        assert 'duplicate_execution' in duplicate.stdout+duplicate.stderr or 'Another n8n execution owns' in duplicate.stdout+duplicate.stderr
        assert live.get_run(rid)==failed and not ledger.exists();calls(live,failed,'effect',0)
        import sqlite3
        with sqlite3.connect('file:'+str(Path(cwd)/'n8n'/'.n8n'/'database.sqlite')+'?mode=ro',uri=True) as db:
            executions=db.execute('SELECT id FROM execution_entity WHERE workflowId=?',(rid,)).fetchall()
        assert len(executions)==2 and len({row[0] for row in executions})==2
    finally:
        release.set();proxy.shutdown();proxy.server_close();server.join(10)
