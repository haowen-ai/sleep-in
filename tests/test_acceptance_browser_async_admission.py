"""An accepted HTTP request stays visibly pending until actual graph completion."""
import json

from test_acceptance_browser import browser, engine_url, isolated_url, pytestmark
from test_acceptance_browser_mapping import HELPERS, durable_runs


def test_browser_async_202_is_not_success_before_all_required_nodes_finish(engine_url, tmp_path):
    gate = tmp_path / 'release-node'
    entered = tmp_path / 'node-entered'
    effects = tmp_path / 'effect-ledger'
    first = ('from pathlib import Path\nimport time\ndef main(inputs):\n'
        f' Path({str(entered)!r}).write_text("entered")\n'
        f' while not Path({str(gate)!r}).exists(): time.sleep(.05)\n'
        ' return {"rows":[1,2,3]}')
    second = ('from pathlib import Path\ndef main(inputs):\n'
        f' with Path({str(effects)!r}).open("a") as f:f.write("effect-001\\n")\n'
        ' return {"count":len(inputs["rows"])}')
    browser(engine_url, HELPERS + 'const fixture=' + json.dumps({
        'first': first, 'second': second, 'gate': str(gate), 'entered': str(entered), 'effects': str(effects)}) + ';' + r'''
 const fs=await import('node:fs');
 const w=await seed({nodes:[node('first',{source:fixture.first}),node('second',{source:fixture.second,inputs:{rows:{source:'node',node_id:'first',path:['rows']}}})],edges:[{source:'first',target:'second'}]});
 const publication=await request('/api/workflows/'+w.id+'/publish','POST',{});await open(w);
 const pending=page.waitForResponse(r=>r.url().endsWith('/run')&&r.request().method()==='POST');
 await page.getByRole('button',{name:'Run published',exact:true}).click();const response=await pending;
 assert.equal(response.status(),202);const admitted=await response.json();assert.equal(admitted.status,'queued');assert.ok(admitted.id);assert.equal(admitted.version_id,publication.version_id);
 let running;for(let i=0;i<600;i++){running=await request('/api/workflow-runs/'+admitted.id);if(fs.existsSync(fixture.entered))break;await page.waitForTimeout(100);}
 assert.ok(fs.existsSync(fixture.entered));running=await request('/api/workflow-runs/'+admitted.id);assert.equal(running.status,'running');assert.equal(running.nodes.first.status,'running');assert.equal(running.nodes.second.attempts.length,0);assert.equal(fs.existsSync(fixture.effects),false);
 const drawer=page.locator('.wf-run-drawer');await drawer.getByText('Running',{exact:true}).first().waitFor();assert.equal(await drawer.getByText('Succeeded',{exact:true}).count(),0);assert.ok((await drawer.textContent()).includes(admitted.id));
 fs.writeFileSync(fixture.gate,'release');let done=await waitRun(admitted);assert.equal(done.status,'succeeded',done.error);for(let i=0;i<100&&!done.adapter_finished_at;i++){await page.waitForTimeout(100);done=await request('/api/workflow-runs/'+admitted.id);}assert.ok(done.adapter_finished_at);assert.ok(done.n8n_execution_id);assert.equal(done.version_id,publication.version_id);
 for(const id of ['first','second']){assert.equal(done.nodes[id].status,'succeeded');assert.equal(done.nodes[id].attempts.length,1);assert.ok(done.nodes[id].finished_at);}
 assert.deepEqual(done.nodes.second.inputs,{rows:[1,2,3]});assert.deepEqual(done.nodes.second.output.data,{count:3});assert.equal((await request('/api/workflow-runs')).length,1);
 await page.goto(origin+'/workflow-runs/'+done.id);await page.locator('.wf-run-summary').getByText('Succeeded',{exact:true}).waitFor();await page.reload();await page.locator('.wf-run-summary').getByText('Succeeded',{exact:true}).waitFor();
 ''')
    assert effects.read_text().splitlines() == ['effect-001']
    assert len(durable_runs(tmp_path)) == 1
