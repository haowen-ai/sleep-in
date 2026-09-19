"""Owned local account sessions and diagnostic scans; no host account or log access."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import httpx
import pytest

from test_acceptance_browser import NODE, PLAYWRIGHT, ROOT
from test_acceptance_local_recovery import supervisor, until, alive, finished
from taskconsole.local import request_stop, status


pytestmark = pytest.mark.skipif(os.environ.get('SLEEP_IN_BROWSER_TEST') != '1'
    or not os.environ.get('SLEEP_IN_TEST_N8N_COMMAND'),
    reason='BLOCKED_ENV: actual isolated Chromium and native supervisor/n8n required')


def account_browser(url, password, body):
    code = r"""
import assert from 'node:assert/strict';
const {chromium}=await import(process.env.PLAYWRIGHT_MODULE);
const browser=await chromium.launch({headless:true});
const context=await browser.newContext({locale:'en-US'}),page=await context.newPage();
page.setDefaultTimeout(30000);
const origin=process.env.TEST_ORIGIN,secret=process.env.TEST_ACCOUNT_PASSWORD;
const login=async(p,password,initial=false)=>{
 await p.goto(origin);
 if(initial)await p.getByRole('button',{name:'Use default account',exact:true}).click();
 else{await p.locator('[name=username]').fill('admin');await p.locator('[name=password]').fill(password);}
 const pending=p.waitForResponse(r=>r.url().endsWith('/api/login'));
 await p.getByRole('button',{name:'Sign in',exact:true}).click();
 return pending;
};
const token=async c=>(await c.cookies()).find(c=>c.name==='console_session').value;
try{
""" + body + r"""
}finally{await context.close();await browser.close();}
"""
    result = subprocess.run([NODE, '--input-type=module', '-e', code], cwd=ROOT,
        env={**os.environ, 'PLAYWRIGHT_MODULE': PLAYWRIGHT, 'TEST_ORIGIN': url,
             'TEST_ACCOUNT_PASSWORD': password}, text=True, capture_output=True, timeout=100)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def diagnostics(s):
    """Only responses and files emitted by this disposable application are included."""
    documents = {}
    for endpoint in ['/healthz', '/readyz', '/api/bootstrap']:
        response = httpx.get(s.initial['url'] + endpoint, timeout=10, trust_env=False)
        assert response.status_code == 200
        documents[endpoint] = response.text
    cli = subprocess.run([sys.executable, '-m', 'taskconsole.local', '--state', str(s.state), 'status'],
                         capture_output=True, text=True, timeout=10)
    assert cli.returncode == 0
    assert json.loads(cli.stdout)['state'] == 'running'
    documents['status.stdout'] = cli.stdout; documents['status.stderr'] = cli.stderr
    for name in ['local-status.json', 'local-launch.log', 'local-service.log']:
        path = s.state / name
        assert path.exists(), name
        documents[name] = path.read_text()
    # Private live control files and database/protocol inputs are intentionally
    # not classified as logs. The scan checks their tokens never enter diagnostics.
    return documents


def assert_no_canaries(documents, canaries):
    for name, text in documents.items():
        for value in canaries:
            assert value not in text, 'Private canary disclosed in owned diagnostic: ' + name


def test_mac_au03_two_browser_sessions_revoke_restart_and_password_diagnostics(supervisor):
    s = supervisor
    password = 'SyntheticAccount_' + uuid.uuid4().hex
    initial_token = s.client.cookies.get('console_session')
    result = account_browser(s.initial['url'], password, r"""
 assert.equal((await login(page,'',true)).status(),200);
 await page.getByRole('button',{name:'New workflow',exact:true}).waitFor();
 const other=await browser.newContext({locale:'en-US'}),second=await other.newPage();
 try{
  assert.equal((await login(second,'',true)).status(),200);
  await second.getByRole('button',{name:'New workflow',exact:true}).waitFor();
  const old=[await token(context),await token(other)];assert.notEqual(old[0],old[1]);
  await page.goto(origin+'/account');
  await page.getByLabel('Current password',{exact:true}).fill('sleepin123456');
  await page.getByLabel('New password',{exact:true}).fill(secret);
  const changed=page.waitForResponse(r=>r.url().endsWith('/api/me/password'));
  await page.locator('form button[type=submit]').click();assert.equal((await changed).status(),200);
  for(const p of [page,second]){
   await p.reload();await p.getByRole('button',{name:'Sign in',exact:true}).waitFor();
   assert.equal(await p.getByRole('button',{name:'Use default account',exact:true}).count(),0);
   assert.equal((await p.request.get(origin+'/api/tasks')).status(),401);
   const bootstrap=await p.request.get(origin+'/api/bootstrap');
   assert.equal((await bootstrap.json()).local_account,null);
   assert.equal((await bootstrap.text()).includes(secret),false);
   assert.equal((await p.locator('body').textContent()).includes(secret),false);
  }
  assert.equal((await login(page,'sleepin123456')).status(),401);
  assert.equal((await login(page,secret)).status(),200);
  await page.getByRole('button',{name:'New workflow',exact:true}).waitFor();
  const current=await token(context);
  console.log(JSON.stringify({old,current}));
 }finally{await other.close();}
""")
    old = [initial_token, *result['old']]
    with s.store.transaction() as tx:
        sessions = tx.all('session')
        assert len(sessions) == 1
        assert sessions[0]['id'] == hashlib.sha256(result['current'].encode()).hexdigest()
        assert not {hashlib.sha256(value.encode()).hexdigest() for value in old} & {row['id'] for row in sessions}
    canaries = [password, *old, result['current'], (s.state / 'dispatch-token').read_text().strip(),
                (s.state / 'variable-key').read_text().strip()]
    before = diagnostics(s); assert_no_canaries(before, canaries)
    request_stop(s.state, 'finish'); until(lambda: not alive(s.initial['pid']), timeout=30)
    replacement = s.launch('--explicit')
    assert replacement['generation'] != s.initial['generation']
    for raw in old:
        with httpx.Client(base_url=s.initial['url'], cookies={'console_session': raw}, trust_env=False) as client:
            assert client.get('/api/tasks').status_code == 401
    restarted = account_browser(s.initial['url'], password, r"""
 await page.goto(origin);await page.getByRole('button',{name:'Sign in',exact:true}).waitFor();
 assert.equal(await page.getByRole('button',{name:'Use default account',exact:true}).count(),0);
 assert.equal((await login(page,'sleepin123456')).status(),401);
 assert.equal((await login(page,secret)).status(),200);
 await page.getByRole('button',{name:'New workflow',exact:true}).waitFor();
 const boot=await(await page.request.get(origin+'/api/bootstrap')).json();
 assert.equal(boot.local_account,null);assert.equal(JSON.stringify(boot).includes(secret),false);
 console.log(JSON.stringify({current:await token(context)}));
""")
    canaries.append(restarted['current'])
    after = diagnostics(s); assert_no_canaries(after, canaries)
    # Persist only a sanitized scan summary, never the scanned credentials/tokens.
    summary = {'surfaces': sorted(after), 'secret_count': len(canaries), 'matches': 0,
               'prechange_sessions_revoked': len(old), 'real_supervisor_restarted': True,
               'os_crash_dump': 'not generated or inspected'}
    report = s.state / 'acceptance-sanitized-diagnostics.json'
    report.write_text(json.dumps(summary))
    assert_no_canaries({'sanitized-summary': report.read_text()}, canaries)


def test_mac_au10_real_worker_stdout_stderr_failure_logs_hide_known_secret(supervisor):
    s = supervisor
    secret = 'SyntheticWorkerSecret_' + uuid.uuid4().hex
    credential = s.svc.save_credential({'name': 'Owned diagnostic canary', 'value': secret})
    source = ('import sys\ndef main(inputs):\n'
        ' print("normal stdout "+inputs["private"],flush=True)\n'
        ' print("normal stderr "+inputs["private"],file=sys.stderr,flush=True)\n'
        ' raise RuntimeError("controlled worker failure "+inputs["private"])')
    wf = s.svc.save({'name': 'Owned diagnostic failure', 'nodes': [{'id': 'source', 'kind': 'python',
        'source': source, 'config': {}, 'inputs': {'private': {'source': 'credential', 'credential_id': credential['id']}}}], 'edges': []})
    s.svc.publish(wf['id']); run = s.svc.admit(wf['id'])
    final = finished(s, run['id'])
    assert final['status'] == 'failed' and final['n8n_execution_id']
    node = final['nodes']['source']
    assert 'normal stdout [redacted]' in node['stdout'] and 'normal stderr [redacted]' in node['stderr']
    documents = diagnostics(s)
    response = s.client.get('/api/workflow-runs/' + run['id'])
    assert response.status_code == 200
    documents['run-response'] = response.text
    logs = list((s.state / 'workflow-runs' / run['id']).rglob('stdout.txt'))
    logs += list((s.state / 'workflow-runs' / run['id']).rglob('stderr.txt'))
    assert len(logs) == 2
    for path in logs:
        documents[str(path.relative_to(s.state))] = path.read_text()
    assert_no_canaries(documents, [secret, s.client.cookies.get('console_session'),
        (s.state / 'dispatch-token').read_text().strip(), (s.state / 'variable-key').read_text().strip()])
