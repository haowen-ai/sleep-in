"""Explicit v1 handoff: convert first, review, then disable old dispatch atomically."""
import copy
import io
import json
from pathlib import Path
import re
import zipfile
from fastapi import Request
from .store import stamp,uid

WRAPPER='''import json, os, runpy, inspect, asyncio
from pathlib import Path
params = json.loads(Path(os.environ["SLEEP_IN_INPUT_FILE"]).read_text())
for binding, name in VARIABLE_BINDINGS.items():
    os.environ[name] = params.pop(binding)
legacy_params = Path(os.environ["SLEEP_IN_INPUT_FILE"]).with_name("legacy-params.json")
legacy_params.write_text(json.dumps(params, ensure_ascii=False))
os.environ["TASK_PARAMS_FILE"] = str(legacy_params)
os.environ["TASK_OUTPUT_DIR"] = os.environ["SLEEP_IN_ARTIFACT_DIR"]
os.environ["TASK_RUN_ID"] = os.environ.get("SLEEP_IN_RUN_ID", "")
scope = runpy.run_path(str(Path(__file__).with_name("main.py")), run_name=RUN_NAME)
if FUNCTION_MODE:
    result = scope["main"](params)
    if inspect.isawaitable(result):
        asyncio.run(result)
root = Path(os.environ["SLEEP_IN_ARTIFACT_DIR"])
artifacts = [{"name":p.relative_to(root).as_posix(), "path":p.relative_to(root).as_posix(), "mediaType":"application/octet-stream"} for p in sorted(root.rglob("*")) if p.is_file()]
Path(os.environ["SLEEP_IN_OUTPUT_FILE"]).write_text(json.dumps({"schemaVersion":1,"data":{},"artifacts":artifacts}))
'''


class WorkflowMigration:
    def __init__(self,store):self.store=store

    def list(self):
        with self.store.transaction() as tx:
            records={x['id']:x for x in tx.all('workflow_migration')}
            return [{'id':t['id'],'name':t['name'],'enabled':t.get('enabled',False),'schedule':t.get('schedule',{}),'migration':records.get(t['id'])} for t in tx.all('task')]

    def convert(self,task_id):
        from .workflows import WorkflowService
        from .workflows_projects import SourceProjects
        with self.store.lock:
            with self.store.transaction() as tx:
                prior=tx.get('workflow_migration',task_id)
                if prior:return prior
                task=tx.get('task',task_id)
                if not task:raise ValueError('Task not found')
                version=tx.get('version',task['version_id'])
                if not version:raise ValueError('Original script version is unavailable')
                configured={}
                for scope in ('instance',version.get('script_id')):
                    for variable in tx.all('variable'):
                        if variable.get('scope')==scope:configured[variable['name']]=variable
                for name in version.get('manifest',{}).get('required_variables',[]):
                    if name not in configured:raise ValueError('Missing required variable: '+name)
            if not re.fullmatch(r'[A-Za-z0-9_-]+',version['id']):raise ValueError('Invalid version identifier')
            folder=self.store.path/'scripts'/version['id']
            if not (folder/'main.py').is_file():raise ValueError('Original source is unavailable')
            params=copy.deepcopy(task.get('params',{}));params['recipients']=task.get('recipients',[])
            variable_bindings={}
            for index,name in enumerate(sorted(configured)):
                key='__sleepin_variable_'+str(index)
                while key in params:key+='_'
                variable_bindings[key]=name
            wrapper=WRAPPER.replace('RUN_NAME',repr('__taskconsole_task__' if version.get('mode')=='function' else '__main__')).replace('FUNCTION_MODE',str(version.get('mode')=='function')).replace('VARIABLE_BINDINGS',repr(variable_bindings))
            archive=io.BytesIO();total=0
            with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED) as zipped:
                for path in folder.rglob('*'):
                    if path.is_symlink():raise ValueError('Review symlink source before migration')
                    if not path.is_file() or '__pycache__' in path.parts:continue
                    total+=path.stat().st_size
                    if total>100*1024*1024:raise ValueError('Migration source exceeds 100 MiB')
                    name=path.relative_to(folder).as_posix()
                    if name=='_sleepin_migrated.py':raise ValueError('Migration wrapper name conflicts with source')
                    zipped.write(path,name)
                zipped.writestr('_sleepin_migrated.py',wrapper)
            project=SourceProjects(self.store).upload(task['name'],'python','_sleepin_migrated.py',archive.getvalue(),'migration.zip',{'entry_mode':'file'})
            schedule=copy.deepcopy(task.get('schedule',{'kind':'manual'}));review=schedule.get('kind')=='cron'
            if review:schedule={'kind':'manual'}
            if schedule.get('kind')=='interval':schedule.update(unit='minutes',anchor=task['anchor'])
            params=copy.deepcopy(task.get('params',{}));params['recipients']=task.get('recipients',[])
            config={'entry_mode':'file','project_id':project['id'],'timeout':task.get('timeout',300),'migration_requires_runtime':bool(version.get('freeze'))}
            node={'id':'legacy','name':task['name'],'kind':'python','source':wrapper,'config':config,'inputs':{k:{'source':'parameter','path':[k]} for k in params},'position':{'x':200,'y':200}}
            wf=WorkflowService(self.store).save({'name':task['name'],'description':'Migrated task; review inputs, runtime and schedule before publication.','nodes':[node],'edges':[],'params':params,'schedule':schedule,'timezone':task.get('timezone','UTC'),'timeout':task.get('timeout',300),'enabled':False})
            record={'id':task_id,'task_id':task_id,'workflow_id':wf['id'],'version_id':version['id'],'created_at':stamp(),'handoff_complete':False,'needs_schedule_review':review,'original_schedule':task.get('schedule',{}),'dependencies_to_rebuild':version.get('freeze','')}
            with self.store.transaction() as tx:
                record['variables_migrated']=[]
                for key,name in variable_bindings.items():
                    variable=configured[name];cid=uid()
                    tx.put('workflow_credential',{'id':cid,'name':'Migrated '+name,'encrypted':variable['encrypted'],'allowed_workflows':[wf['id']],'created_at':stamp(),'revision':uid()})
                    wf['nodes'][0]['inputs'][key]={'source':'credential','credential_id':cid,'type':'string'}
                    record['variables_migrated'].append({'name':name,'credential_id':cid,'source_variable_id':variable['id']})
                wf['migration']=record;tx.put('workflow',wf);tx.put('workflow_migration',record)
            return record

    def handoff(self,task_id):
        with self.store.transaction() as tx:
            record=tx.get('workflow_migration',task_id)
            if not record:raise ValueError('Convert and review this task first')
            if any(r['task_id']==task_id and r['status'] in {'queued','running','cancelling'} for r in tx.all('execution')):raise ValueError('Finish or cancel active legacy runs first')
            task=tx.get('task',task_id);task.update(enabled=False,next_run=None,revision=task.get('revision',0)+1);tx.put('task',task)
            record.update(handoff_complete=True,handed_off_at=stamp());tx.put('workflow_migration',record)
            wf=tx.get('workflow',record['workflow_id']);wf['migration']=record;wf['enabled']=False;tx.put('workflow',wf)
        return record


def register_migration_routes(app,store,require):
    service=WorkflowMigration(store)
    def auth(request):
        with store.transaction() as tx:require(request,tx,True)
    @app.get('/api/workflow-migrations')
    def listing(request:Request):auth(request);return service.list()
    @app.post('/api/workflow-migrations/{task_id}')
    def convert(task_id:str,request:Request):auth(request);return service.convert(task_id)
    @app.post('/api/workflow-migrations/{task_id}/handoff')
    def handoff(task_id:str,request:Request):auth(request);return service.handoff(task_id)
