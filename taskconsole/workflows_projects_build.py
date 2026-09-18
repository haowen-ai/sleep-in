"""Publication-time project compilation and immutable build cache."""
import ast
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from .workflows_projects import relative_path,file_hash,manifest_tree


def expand_argv(argv,values):
    if not isinstance(argv,list) or not argv or any(not isinstance(s,str) for s in argv):raise ValueError('Build/run command must be argv strings')
    result=[]
    for value in argv:
        for key,replacement in values.items():value=value.replace('{'+key+'}',str(replacement))
        result.append(value)
    return result


def build_project_node(store,node):
    result=copy.deepcopy(node);language=result['kind'];runtime=result.get('_runtime',{});config={**runtime.get('config',{}),**result.get('_project',{}).get('config',{}),**result.get('config',{})}
    result['config']=config
    if language in {'python','javascript','shell'}:
        if language=='python':
            source=(Path(result['_project']['directory'])/result['_project']['entrypoint']).read_text() if result.get('_project') else result.get('source','')
            ast.parse(source)
        return result
    project=result.get('_project');source=result.get('source','');main=config.get('main_class','Main')
    if language=='java' and (not isinstance(main,str) or not all(part.isidentifier() for part in main.split('.'))):raise ValueError('Invalid Java main class')
    identity={'language':language,'runtime_id':runtime.get('id'),'runtime_digest':runtime.get('digest'),'project':project.get('digest') if project else None,'source':source,'config':config,'tool_sha256':runtime.get('tool_sha256')}
    digest=hashlib.sha256(json.dumps(identity,sort_keys=True).encode()).hexdigest()
    directory=store.path/'project-builds'/digest;metadata=directory/'build-result.json'
    if metadata.exists():
        compiled=json.loads(metadata.read_text())
        for name,sha in compiled['manifest'].items():
            if file_hash(directory/name)!=sha:raise ValueError('Immutable compiled project changed')
        result['_build']=compiled;return result
    directory.mkdir(parents=True,exist_ok=True);project_dir=directory/'source';project_dir.mkdir(exist_ok=True)
    if runtime.get('directory') and (Path(runtime['directory'])/'shared').exists():shutil.copytree(Path(runtime['directory'])/'shared',project_dir,dirs_exist_ok=True)
    if project:shutil.copytree(project['directory'],project_dir,dirs_exist_ok=True);entrypoint=project['entrypoint']
    else:
        entrypoint=main.split('.')[-1]+'.java' if language=='java' else 'main.cpp' if language=='cpp' else 'main.c' if language=='c' else config.get('entrypoint','main')
        (project_dir/entrypoint).write_text(source)
    output=directory/'program';logs=[];env={**os.environ,'PYTHONDONTWRITEBYTECODE':'1'}
    if runtime.get('java'):
        env['JAVA_HOME']=str(Path(runtime['java']).parent.parent);env['PATH']=str(Path(runtime['java']).parent)+os.pathsep+env.get('PATH','')
    def command(argv):
        proc=subprocess.run(argv,cwd=project_dir,env=env,capture_output=True,text=True,timeout=int(config.get('build_timeout',600)))
        logs.append('$ '+json.dumps(argv)+'\n'+proc.stdout+proc.stderr)
        if proc.returncode:raise ValueError('Project build failed: '+(proc.stderr or proc.stdout)[-4000:])
    if language in {'c','cpp'}:
        sources=config.get('sources') or [p.relative_to(project_dir).as_posix() for p in sorted(project_dir.rglob('*')) if p.suffix in ({'.cpp','.cc','.cxx'} if language=='cpp' else {'.c'})]
        if not sources:raise ValueError('Project has no compilable sources')
        paths=[str(project_dir/relative_path(p)) for p in sources]
        includes=[str(project_dir/relative_path(p)) for p in config.get('include_dirs',[])]
        argv=[runtime.get('compiler') or runtime.get('executable'),'-std=c++17' if language=='cpp' else '-std=c11',*paths]
        for path in includes:argv+=['-I',path]
        argv+=config.get('compile_args',[])+['-o',str(output)]
        command(argv);run=[str(output)]
    elif language=='java':
        kind=config.get('project_type','source');java=runtime.get('java')
        if not java:raise ValueError('Ready JDK runtime required')
        if kind=='source':
            sources=[str(p) for p in sorted(project_dir.rglob('*.java'))]
            if not sources:raise ValueError('Project has no Java sources')
            classes=directory/'classes';classes.mkdir(exist_ok=True)
            command([runtime['compiler'],'-encoding','UTF-8','-d',str(classes),*sources]);run=[java,'-cp',str(classes),main]
        else:
            if kind=='maven':
                tool=config.get('maven_executable') or shutil.which('mvn')
                if not tool:raise ValueError('Install/select an isolated Maven toolchain')
                command([tool,'--batch-mode','-Dmaven.repo.local='+str(directory/'maven-repository'),'package','-DskipTests'])
            elif kind=='gradle':
                tool=config.get('gradle_executable') or shutil.which('gradle')
                if not tool:raise ValueError('Install/select an isolated Gradle toolchain')
                env['GRADLE_USER_HOME']=str(directory/'gradle-home')
                command([tool,'--no-daemon','--console=plain','build','-x','test'])
            elif kind!='jar':raise ValueError('Unsupported Java project type')
            artifact=config.get('artifact') or entrypoint if kind=='jar' else config.get('artifact')
            jars=[project_dir/relative_path(artifact)] if artifact else sorted(project_dir.glob('target/*.jar'))+sorted(project_dir.glob('build/libs/*.jar'))
            jars=[p for p in jars if p.is_file() and not p.name.endswith(('-sources.jar','-javadoc.jar'))]
            if len(jars)!=1:raise ValueError('Choose the single executable JAR artifact')
            target=directory/'application.jar';shutil.copy2(jars[0],target)
            run=[java,'-cp',str(target),main] if config.get('main_class') else [java,'-jar',str(target)]
    elif language=='custom':
        values={'entrypoint':project_dir/entrypoint,'project':project_dir,'output':output}
        if config.get('build_argv'):command(expand_argv(config['build_argv'],values))
        run=expand_argv(config['run_argv'],values)
    else:raise ValueError('Unsupported compiled runtime')
    compiled={'argv':run,'digest':digest,'log':'\n'.join(logs),'directory':str(directory),'manifest':manifest_tree(directory),'toolchain':{'runtime_version_id':runtime.get('id'),'platform':runtime.get('platform'),'architecture':runtime.get('architecture')}}
    metadata.write_text(json.dumps(compiled))
    result['_build']=compiled;return result
