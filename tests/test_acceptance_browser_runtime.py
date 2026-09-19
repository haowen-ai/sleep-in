"""Browser runtime acceptance with actual detection and isolated native toolchains."""
import json
import os
from pathlib import Path
import sys

import pytest

from test_acceptance_browser import browser, engine_url, isolated_url, pytestmark
from test_acceptance_browser_mapping import HELPERS, durable_runs


def test_browser_genuinely_missing_compilers_block_publication_and_explain_repair(request, monkeypatch, tmp_path):
    empty=tmp_path/'empty-bin';empty.mkdir()
    monkeypatch.setenv('PATH',str(empty))
    url=request.getfixturevalue('isolated_url')
    browser(url,HELPERS+r"""
 const detected=await request('/api/runtimes');for(const language of ['java','c','cpp']){const runtime=detected.find(r=>r.language===language);assert.equal(runtime.status,'unavailable');assert.equal(runtime.executable,null);assert.ok(runtime.reason.includes('Install a working '+language+' toolchain'));}
 await page.goto(origin+'/runtimes');for(const language of ['java','c','cpp']){const card=page.locator('.wf-resource').filter({has:page.getByRole('heading',{name:'Native '+language,exact:true})});await card.getByText('Unavailable',{exact:true}).waitFor();assert.ok((await card.textContent()).includes('Install a working '+language+' toolchain'));assert.ok((await card.textContent()).includes('Unavailable runtimes must be installed by the workspace owner before publishing.'));await card.getByText('Capabilities',{exact:true}).click();assert.ok((await card.textContent()).includes('"cpu_memory_isolation": false'));}
 for(const language of ['java','c','cpp']){const w=await seed({nodes:[node(language,{kind:language,config:{runtime_id:'native_'+language},source:'intentionally unavailable toolchain fixture'})],edges:[]});await open(w);await selectNode(language);await page.getByRole('button',{name:'Environment',exact:true}).click();const options=await page.getByLabel('Runtime profile',{exact:true}).locator('option').allTextContents();assert.ok(options.some(x=>x.includes('Unavailable')));await page.getByLabel('Runtime profile',{exact:true}).selectOption('native_'+language);await save(w);const response=page.waitForResponse(r=>r.url().endsWith('/publish'));await page.getByRole('button',{name:'Publish',exact:true}).click();assert.equal((await response).status(),422);assert.ok((await page.locator('.wf-validation').textContent()).includes('Install a working '+language+' toolchain'));assert.ok((await page.locator('article[data-node-id="'+language+'"]').textContent()).includes('Unavailable'));assert.equal((await request('/api/workflows/'+w.id)).published_version_id,null);}
 assert.deepEqual(await request('/api/workflow-runs'),[]);assert.ok((await page.locator('.wf-inspector').textContent()).includes('Native processes: no container-level CPU or memory isolation.'));
 """)


def test_browser_all_native_profiles_are_language_scoped_and_freeze_real_builds(request, monkeypatch, tmp_path):
    import shutil
    import subprocess
    from test_acceptance_browser import NODE
    from test_runtime_language_matrix import producer, VALUE
    javac=os.environ.get('SLEEP_IN_TEST_JAVAC')
    if not javac:
        pytest.skip('Real Java runtime browser requires SLEEP_IN_TEST_JAVAC')
    monkeypatch.setenv('PATH',str(Path(javac).parent)+os.pathsep+os.environ.get('PATH',''))
    for command in (javac,shutil.which('cc'),shutil.which('c++')):
        if not command or subprocess.run([command,'-version' if command==javac else '--version'],capture_output=True).returncode:
            pytest.skip('Real Java/C/C++ toolchains must be available for runtime browser acceptance')
    url=request.getfixturevalue('engine_url')
    sources={language:producer(language) for language in ['shell','java','c','cpp']}
    sources['java']=sources['java'].replace('class Main','class Entry')
    disable='''
import os,stat,sys
from pathlib import Path
from taskconsole.store import Store
root=Path(sys.argv[1]).resolve()
with Store(root,'sqlite:///'+str(root/'browser.sqlite')).transaction() as tx:
    runtime=tx.get('runtime_version',sys.argv[2])
path=Path(runtime['executable']).resolve()
assert path.is_relative_to(root/'runtime-packs')
os.chmod(path,stat.S_IMODE(path.stat().st_mode)&~0o111)
'''
    configs={'python':{'executable':sys.executable},'javascript':{'executable':NODE},'java':{'executable':javac},'shell':{},'c':{'compile_args':['-DFIXTURE_RUNTIME=1']},'cpp':{'compile_args':['-DFIXTURE_RUNTIME=1']}}
    result=browser(url,HELPERS+'const configs='+json.dumps(configs)+';const sources='+json.dumps(sources)+';const value='+json.dumps(VALUE,ensure_ascii=False)+';const python='+json.dumps(sys.executable)+';const state='+json.dumps(str(tmp_path))+';const disable='+json.dumps(disable)+';'+r"""
 const profiles={};await page.goto(origin+'/runtimes');
 for(const language of ['python','javascript','shell','java','c','cpp']){
  await page.getByRole('button',{name:'Create runtime profile',exact:true}).click();const form=page.locator('form').filter({has:page.getByRole('heading',{name:'Create runtime profile',exact:true})});await form.getByLabel('Name',{exact:true}).fill('Browser '+language);await form.getByLabel('Language',{exact:true}).selectOption(language);await form.getByLabel('Build configuration (JSON)',{exact:true}).fill(JSON.stringify(configs[language]));const created=page.waitForResponse(r=>r.url().endsWith('/api/runtime-profiles')&&r.request().method()==='POST');await form.getByRole('button',{name:'Create draft',exact:true}).click();const profile=await(await created).json();profiles[language]=profile;const card=page.locator('article.wf-form').filter({has:page.getByRole('heading',{name:'Browser '+language,exact:true})});await card.locator('summary').first().click();const response=page.waitForResponse(r=>r.url().endsWith('/api/runtime-profiles/'+profile.id+'/build'));await card.getByRole('button',{name:'Build and self-test',exact:true}).click();const built=await(await response).json();assert.equal(built.status,'ready',JSON.stringify(built));assert.equal(built.verification.status,'passed');assert.ok(built.digest);profiles[language].version=built;
 }
 const extra=await request('/api/runtime-profiles/'+profiles.python.id+'/versions','POST',{config:{...configs.python,shared_files:{'version_marker.py':'VALUE=2\n'}}});const secondPython=await request('/api/runtime-profiles/'+profiles.python.id+'/build','POST',{version_id:extra.id});assert.equal(secondPython.status,'ready');
 const {id,...template}=(await request('/api/workflow-templates'))[0];for(const language of ['shell','java','c','cpp'])template.nodes.push(node(language,{kind:language,source:sources[language]}));const w=await seed(template);await open(w);const nodeLanguages={summary:'python',report:'javascript',shell:'shell',java:'java',c:'c',cpp:'cpp'};
 for(const [id,language] of Object.entries(nodeLanguages)){await selectNode(id);if(language==='java'){await page.getByLabel('Java main class',{exact:true}).fill('Entry');}if(['python','javascript'].includes(language))assert.equal(await page.getByLabel('Entrypoint',{exact:true}).inputValue(),template.nodes.find(n=>n.id===id).config?.entry_mode||'function');await page.getByRole('button',{name:'Environment',exact:true}).click();assert.deepEqual(await page.getByLabel('Runtime profile',{exact:true}).locator('option').evaluateAll(xs=>xs.map(x=>x.value)),['','native_'+language]);const versions=await page.getByLabel('Immutable runtime version',{exact:true}).locator('option').evaluateAll(xs=>xs.map(x=>x.value));assert.deepEqual(versions,['',profiles[language].version.id,...(language==='python'?[secondPython.id]:[])]);await page.getByLabel('Runtime profile',{exact:true}).selectOption('native_'+language);await page.getByLabel('Immutable runtime version',{exact:true}).selectOption(profiles[language].version.id);assert.ok((await page.locator('.wf-inspector').textContent()).includes('Native processes: no container-level CPU or memory isolation.'));}
 const first=await save(w);await selectNode('summary');await page.getByRole('button',{name:'Environment',exact:true}).click();await page.getByLabel('Immutable runtime version',{exact:true}).selectOption(secondPython.id);const saved=await save(w);assert.deepEqual(saved.nodes.filter(n=>n.id!=='summary'),first.nodes.filter(n=>n.id!=='summary'));await open(w);for(const [id,language] of Object.entries(nodeLanguages)){await selectNode(id);await page.getByRole('button',{name:'Environment',exact:true}).click();assert.equal(await page.getByLabel('Immutable runtime version',{exact:true}).inputValue(),language==='python'?secondPython.id:profiles[language].version.id);}
 const publication=page.waitForResponse(r=>r.url().endsWith('/publish'));await page.getByRole('button',{name:'Publish',exact:true}).click();const published=await(await publication).json();assert.ok(published.version_id,JSON.stringify(published));const runPublished=async()=>{const response=page.waitForResponse(r=>r.url().endsWith('/run'));await page.getByRole('button',{name:'Run published',exact:true}).click();const r=await response;assert.equal(r.status(),202,await r.text());return waitRun(await r.json());};const run=await runPublished();assert.equal(run.status,'succeeded',JSON.stringify(run));assert.equal(run.version_id,published.version_id);assert.deepEqual(run.nodes.summary.output.data.summary,{count:3,total:'30.75'});for(const language of ['shell','java','c','cpp'])assert.deepEqual(run.nodes[language].output.data,value);
 for(const language of ['java','c','cpp']){await sourceCode(language,language==='java'?'public class Entry { invalid syntax }':'int main( {');await save(w);const rejected=page.waitForResponse(r=>r.url().endsWith('/publish'));await page.getByRole('button',{name:'Publish',exact:true}).click();const response=await rejected;assert.equal(response.status(),422);assert.ok((await page.locator('.wf-validation').textContent()).includes('Project build failed'));assert.equal((await request('/api/workflows/'+w.id)).published_version_id,published.version_id);await sourceCode(language,sources[language]);await save(w);}
 (await import('node:child_process')).execFileSync(python,['-c',disable,state,secondPython.id]);const failed=await runPublished();assert.equal(failed.version_id,published.version_id);assert.equal(failed.status,'failed');assert.equal(failed.nodes.summary.status,'failed');assert.match(failed.nodes.summary.error,/Pinned runtime executable unavailable/);assert.equal(failed.nodes.report.attempts.length,0);await page.goto(origin+'/workflow-runs/'+failed.id);await page.locator('.wf-run-summary').getByText('Failed',{exact:true}).waitFor();assert.ok((await page.locator('.wf-node-detail[open]').textContent()).includes('restore the pinned runtime or publish a new version'));console.log(JSON.stringify({published:published.version_id,run:run.id,failed:failed.id,versions:Object.fromEntries(Object.entries(profiles).map(([key,p])=>[key,key==='python'?secondPython.id:p.version.id]))}));
 """)
    ids=json.loads(result)
    runs=durable_runs(tmp_path)
    assert len(runs)==2
    for run in runs:
        assert run['version_id']==ids['published']
        for node in run['snapshot']['nodes']:
            if node['kind']=='sql':continue
            assert node['config']['runtime_version_id']==ids['versions'][node['kind']]
            assert node['_runtime']['id']==ids['versions'][node['kind']]
            assert node['_runtime']['verification']['status']=='passed'
            assert node['_runtime']['digest'] and node['_runtime']['tool_sha256']
            if node['kind'] in {'java','c','cpp'}:
                assert node['_build']['toolchain']['runtime_version_id']==ids['versions'][node['kind']]
                assert node['_build']['digest'] and node['_build']['log']
    original=next(r for r in runs if r['id']==ids['run'])
    assert next(n for n in original['snapshot']['nodes'] if n['id']=='java')['config']['main_class']=='Entry'
    assert next(n for n in original['snapshot']['nodes'] if n['id']=='c')['config']['compile_args']==['-DFIXTURE_RUNTIME=1']
