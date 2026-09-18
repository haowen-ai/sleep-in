"""Opt-in real supervisor smoke; explicitly disables physical power assertions."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import pytest
from taskconsole.local import read_json, write_json, status, request_stop


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_TEST_N8N_COMMAND'),reason='Set real Node/n8n JSON argv to run isolated supervisor verification')
def test_real_supervisor_repeated_launch_stop_and_stopped_login(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    write_json(tmp_path/'local-config.json',{'app_port':port,'n8n_command':json.loads(os.environ['SLEEP_IN_TEST_N8N_COMMAND']),'disable_power_assertion':True})
    command=[sys.executable,'-m','taskconsole.local','--state',str(tmp_path)]
    def cli(*args): return subprocess.run([*command,*args],capture_output=True,text=True,timeout=85)
    try:
        first=cli('start','--explicit')
        assert first.returncode==0,first.stderr+'\n'+(tmp_path/'local-launch.log').read_text()
        initial=json.loads(first.stdout)
        assert initial['state']=='running'
        assert initial['components']['app']=='ready'
        assert initial['components']['worker']=='ready'
        assert initial['components']['power']=='disabled-for-test'
        assert not initial['assertion']
        second=cli('start')
        assert second.returncode==0,second.stderr
        assert json.loads(second.stdout)['pid']==initial['pid']
        assert request_stop(tmp_path,'finish')['state']=='draining'
        deadline=time.monotonic()+20
        while status(tmp_path)['state']!='stopped' and time.monotonic()<deadline: time.sleep(.2)
        assert status(tmp_path)['state']=='stopped'
        stopped=cli('start')
        assert stopped.returncode==0
        assert json.loads(stopped.stdout)['state']=='stopped'
    finally:
        request_stop(tmp_path,'cancel')
        deadline=time.monotonic()+20
        while status(tmp_path)['state']!='stopped' and time.monotonic()<deadline: time.sleep(.2)


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_TEST_N8N_COMMAND'),reason='Real Node/n8n required')
@pytest.mark.parametrize('mode,duration,expected',[('finish',4,'succeeded'),('cancel',120,'cancelled')])
def test_real_supervisor_drains_or_confirms_cancel_before_stop(tmp_path,mode,duration,expected):
    from taskconsole.store import Store
    from taskconsole.workflows import WorkflowService,WorkflowError
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    write_json(tmp_path/'local-config.json',{'app_port':port,'n8n_command':json.loads(os.environ['SLEEP_IN_TEST_N8N_COMMAND']),'disable_power_assertion':True})
    command=[sys.executable,'-m','taskconsole.local','--state',str(tmp_path)]
    store=None
    try:
        launch=subprocess.run([*command,'start','--explicit'],capture_output=True,text=True,timeout=85)
        assert launch.returncode==0,launch.stderr
        store=Store(tmp_path,f'sqlite:///{tmp_path}/console.db');service=WorkflowService(store)
        workflow=service.save({'name':'Local drain fixture','nodes':[{'id':'wait','name':'Wait','kind':'python','source':f'import time\ndef main(inputs):\n    time.sleep({duration})\n    return {{"finished": True}}\n','config':{},'inputs':{},'outputs':{'type':'object'}}],'edges':[],'timeout':180})
        service.publish(workflow['id']);run=service.admit(workflow['id'])
        deadline=time.monotonic()+60
        while service.get_run(run['id'])['nodes']['wait']['status']!='running' and time.monotonic()<deadline: time.sleep(.2)
        assert service.get_run(run['id'])['nodes']['wait']['status']=='running'
        assert request_stop(tmp_path,mode)['state']=='draining'
        with pytest.raises(WorkflowError):service.admit(workflow['id'])
        deadline=time.monotonic()+30
        while status(tmp_path)['state']!='stopped' and time.monotonic()<deadline: time.sleep(.2)
        assert status(tmp_path)['state']=='stopped'
        assert service.get_run(run['id'])['status']==expected
        assert not status(tmp_path)['assertion']
    finally:
        request_stop(tmp_path,'cancel')
        deadline=time.monotonic()+20
        while status(tmp_path)['state']!='stopped' and time.monotonic()<deadline:time.sleep(.2)
        if store:store.engine.dispose()


@pytest.mark.skipif(not os.environ.get('SLEEP_IN_TEST_N8N_COMMAND'),reason='Real Node/n8n required')
def test_crash_restart_changes_generation_and_reclaims_children(tmp_path):
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    write_json(tmp_path/'local-config.json',{'app_port':port,'n8n_command':json.loads(os.environ['SLEEP_IN_TEST_N8N_COMMAND']),'disable_power_assertion':True})
    command=[sys.executable,'-m','taskconsole.local','--state',str(tmp_path)]
    try:
        first=subprocess.run([*command,'start','--explicit'],capture_output=True,text=True,timeout=85)
        assert first.returncode==0,first.stderr
        initial=json.loads(first.stdout);os.kill(initial['pid'],signal.SIGKILL)
        deadline=time.monotonic()+15
        while time.monotonic()<deadline:
            try:
                with socket.socket() as sock:
                    sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                    sock.bind(('127.0.0.1',port))
                break
            except OSError:time.sleep(.2)
        assert status(tmp_path)['state']=='stopped'
        second=subprocess.run([*command,'start'],capture_output=True,text=True,timeout=85)
        assert second.returncode==0,second.stderr
        restarted=json.loads(second.stdout)
        assert restarted['generation']!=initial['generation']
        assert restarted['pid']!=initial['pid']
        assert restarted['components']['worker']=='ready'
        assert restarted['recovery']['previous_seen'] is not None
    finally:
        request_stop(tmp_path,'cancel')
        deadline=time.monotonic()+20
        while status(tmp_path)['state']!='stopped' and time.monotonic()<deadline:time.sleep(.2)
