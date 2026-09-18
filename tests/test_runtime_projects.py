"""Real publication-time compilation and execution for project forms."""
import json
import os
from pathlib import Path
import subprocess
import pytest
from taskconsole.store import Store
from taskconsole.workflows_packs import RuntimePacks,prepare_node
from taskconsole.workflows_projects import SourceProjects
from taskconsole.workflows_runtime import run_script

@pytest.fixture
def store(tmp_path):
    s=Store(tmp_path,'sqlite:///'+str(tmp_path/'state.sqlite'));yield s;s.engine.dispose()


def pack(store,language,config=None):
    packs=RuntimePacks(store);p=packs.create({'language':language,'config':config or {}});v=packs.build(p['id']);assert v['status']=='ready',v;return v


@pytest.mark.parametrize('language',['c','cpp'])
def test_multisource_header_project_and_cache(store,tmp_path,language):
    extension='cpp' if language=='cpp' else 'c'
    files={'src/main.'+extension:'#include <stdio.h>\n#include <stdlib.h>\n#include "value.h"\nint main(void){FILE*f=fopen(getenv("SLEEP_IN_OUTPUT_FILE"),"w");fprintf(f,"{\\"schemaVersion\\":1,\\"data\\":{\\"value\\":%d},\\"artifacts\\":[]}",value());return fclose(f);}','src/value.'+extension:'#include "value.h"\nint value(void){return 37;}','include/value.h':'int value(void);'}
    p=SourceProjects(store).create({'language':language,'entrypoint':'src/main.'+extension,'files':files,'config':{'include_dirs':['include']}});v=pack(store,language)
    node={'id':'multi','kind':language,'config':{'project_id':p['id'],'runtime_version_id':v['id']}}
    first=prepare_node(store,node);second=prepare_node(store,node)
    assert first['_build']['digest']==second['_build']['digest']
    assert run_script(first,{},tmp_path/'run',store.path)['output']['data']=={'value':37}
    executable=Path(first['_build']['argv'][0]);executable.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='Immutable compiled'):prepare_node(store,node)


def test_custom_argv_real_protocol(store,tmp_path):
    import sys
    source='import os,json\njson.dump({"schemaVersion":1,"data":{"ok":True},"artifacts":[]},open(os.environ["SLEEP_IN_OUTPUT_FILE"],"w"))'
    v=pack(store,'custom',{'run_argv':[sys.executable,'{entrypoint}'],'self_test_source':source})
    node=prepare_node(store,{'id':'custom','kind':'custom','source':source,'config':{'runtime_version_id':v['id']}})
    assert run_script(node,{},tmp_path/'run',store.path)['output']['data']=={'ok':True}

JAVA='import java.nio.file.*;public class Main{public static void main(String[] args)throws Exception{Files.writeString(Path.of(System.getenv("SLEEP_IN_OUTPUT_FILE")),"{\\"schemaVersion\\":1,\\"data\\":{\\"value\\":41},\\"artifacts\\":[]}");}}'

@pytest.mark.parametrize('kind',['source','jar','maven','gradle'])
def test_java_project_forms(store,tmp_path,kind):
    javac=os.environ.get('SLEEP_IN_TEST_JAVAC')
    if not javac:pytest.skip('Verified JDK not configured')
    tool=os.environ.get('SLEEP_IN_TEST_'+kind.upper()) if kind in {'maven','gradle'} else None
    if kind in {'maven','gradle'} and not tool:pytest.skip('Verified '+kind+' not configured')
    v=pack(store,'java',{'executable':javac});config={'project_type':kind,'main_class':'Main'}
    if kind=='jar':
        folder=tmp_path/'jarbuild';folder.mkdir();(folder/'Main.java').write_text(JAVA)
        subprocess.run([javac,str(folder/'Main.java')],check=True,capture_output=True)
        jar=folder/'app.jar';subprocess.run([str(Path(javac).parent/'jar'),'--create','--file',str(jar),'-C',str(folder),'Main.class'],check=True,capture_output=True)
        project=SourceProjects(store).upload('jar','java','app.jar',jar.read_bytes(),'app.jar',config)
    else:
        files={'Main.java':JAVA};entry='Main.java'
        if kind=='maven':
            config['maven_executable']=tool;entry='pom.xml';files={'src/main/java/Main.java':JAVA,'pom.xml':'<project xmlns="http://maven.apache.org/POM/4.0.0"><modelVersion>4.0.0</modelVersion><groupId>sleepin</groupId><artifactId>fixture</artifactId><version>1.0</version><properties><maven.compiler.release>21</maven.compiler.release></properties></project>'}
        if kind=='gradle':
            config['gradle_executable']=tool;entry='build.gradle';files={'src/main/java/Main.java':JAVA,'settings.gradle':'rootProject.name="fixture"','build.gradle':'plugins { id "java" }\nversion="1.0"\n'}
        project=SourceProjects(store).create({'language':'java','entrypoint':entry,'files':files,'config':config})
    node=prepare_node(store,{'id':'java','kind':'java','config':{'project_id':project['id'],'runtime_version_id':v['id']}})
    result=run_script(node,{},tmp_path/'run',store.path)
    assert result['status']=='succeeded',result
    assert result['output']['data']=={'value':41}
