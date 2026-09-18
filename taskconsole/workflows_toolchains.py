"""Owner-requested user-space toolchains, pinned to official archive checksums."""
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile
from .store import stamp
from .workflows_projects import relative_path,file_hash

MANIFESTS=[
 {'id':'apple-command-line-tools-arm64','name':'Apple Command Line Tools (C/C++)','language':'c','languages':['c','cpp'],'platform':'darwin','architecture':'arm64','installer':'system_dialog','system_consent':True,'license':'Apple Command Line Tools license; explicit acceptance in the macOS installation dialog','source':'https://developer.apple.com/documentation/xcode/installing-the-command-line-tools','description':'Requests the macOS installation dialog only after you click Install. Waiting does not mean ready; C and C++ must compile and run successfully.'},
 {'id':'maven-3.9.16','name':'Apache Maven 3.9.16','language':'maven','url':'https://dlcdn.apache.org/maven/maven-3/3.9.16/binaries/apache-maven-3.9.16-bin.tar.gz','hash_algorithm':'sha512','sha512':'831a8591fe20c8243b1dbe7d71e3244f31d1665b0804b2e825e38cbbe5ce0cafb8338851f90780735568773e0a6cd07bbec107cda0b896b008b861075358b6f6','format':'tar','home_glob':'apache-maven-3.9.16','executable':'bin/mvn','verify_argv':['--version']},
 {'id':'temurin-21.0.12.1-mac-aarch64','name':'Eclipse Temurin JDK 21.0.12.1+1','language':'java','platform':'darwin','architecture':'arm64','url':'https://github.com/adoptium/temurin21-binaries/releases/download/jdk-21.0.12.1%2B1/OpenJDK21U-jdk_aarch64_mac_hotspot_21.0.12.1_1.tar.gz','sha256':'3623232f33a9c3baadf304480b2535f9a3cba8a58d42ecbb438ba267315d9998','format':'tar','home_glob':'*/Contents/Home','executable':'bin/java','verify_argv':['-version']},
 {'id':'node-24.19.0-mac-arm64','name':'Node.js 24.19.0 with npm','language':'javascript','platform':'darwin','architecture':'arm64','url':'https://nodejs.org/dist/v24.19.0/node-v24.19.0-darwin-arm64.tar.gz','sha256':'8294b7aa9b03997481c06babf1e8b270c859358f27da57a11509afe537ac381d','format':'tar','home_glob':'node-v24.19.0-darwin-arm64','executable':'bin/node','verify_argv':['--version']},
 {'id':'gradle-9.7.1','name':'Gradle 9.7.1','language':'gradle','url':'https://services.gradle.org/distributions/gradle-9.7.1-bin.zip','sha256':'acd53f1edaf02f1a8ff99879f8a34b302661a057d9b063ae9e35b552f804d20a','format':'zip','home_glob':'gradle-9.7.1','executable':'bin/gradle','verify_argv':['--version']},
]

class Toolchains:
    def __init__(self,store):self.store=store
    def list(self):
        with self.store.transaction() as tx:installed=tx.all('runtime_toolchain')
        return {'candidates':[m for m in MANIFESTS if m.get('platform',platform.system().lower())==platform.system().lower() and m.get('architecture',platform.machine())==platform.machine()],'installed':installed}
    def install(self,manifest_id):
        manifest=next((m for m in self.list()['candidates'] if m['id']==manifest_id),None)
        if not manifest:raise ValueError('Unknown or incompatible toolchain manifest')
        if manifest.get('installer')=='system_dialog':return self._request_apple_tools(manifest)
        return self.install_manifest(manifest)
    def _apple_compiler_test(self,manifest):
        """Probe selected real tool binaries; never invokes an installation shim."""
        selected=subprocess.run(['/usr/bin/xcode-select','-p'],capture_output=True,text=True,timeout=10)
        if selected.returncode:return None
        developer=Path(selected.stdout.strip())
        if not developer.is_absolute():return None
        homes=[developer/'usr',developer/'Toolchains/XcodeDefault.xctoolchain/usr']
        home=next((h for h in homes if (h/'bin/clang').is_file() and (h/'bin/clang++').is_file()),None)
        if home is None:return None
        root=self.store.path/'toolchains'/manifest['id']/'self-test';root.mkdir(parents=True,exist_ok=True)
        executables={'c':str(home/'bin/clang'),'cpp':str(home/'bin/clang++')};versions={};hashes={}
        for language,compiler in executables.items():
            result=subprocess.run([compiler,'--version'],capture_output=True,text=True,timeout=10)
            if result.returncode:raise ValueError('Apple compiler version check failed: '+result.stderr[-1000:])
            versions[language]=result.stdout.strip();hashes[language]=file_hash(compiler)
            source=root/('main.c' if language=='c' else 'main.cpp');program=root/(language+'-selftest')
            source.write_text('#include <stdio.h>\nint main(void){puts("7");return 0;}\n' if language=='c' else '#include <iostream>\nint main(){std::cout << 7 << "\\n";return 0;}\n')
            result=subprocess.run([compiler,'-std=c11' if language=='c' else '-std=c++17',str(source),'-o',str(program)],capture_output=True,text=True,timeout=30)
            if result.returncode:raise ValueError('Apple '+language+' compile/link self-test failed: '+result.stderr[-1500:])
            result=subprocess.run([str(program)],capture_output=True,text=True,timeout=10)
            if result.returncode or result.stdout!='7\n':raise ValueError('Apple '+language+' executable self-test failed')
        return {'home':str(home),'executables':executables,'executable':executables['c'],'version':versions['c'],'compiler_sha256':hashes,'verification':{'status':'passed','languages':['c','cpp'],'checks':['compile','link','execute'],'tested_at':stamp()}}

    def _request_apple_tools(self,manifest):
        # Only explicit administrator POST /install reaches this method.
        key=manifest['id']
        with self.store.transaction() as tx:
            previous=tx.get('runtime_toolchain',key)
            if previous and previous['status']=='installing':return previous
            record={**(previous or {}),'id':key,'name':manifest['name'],'language':'c','languages':['c','cpp'],'installer':'system_dialog','system_consent':True,'platform':'darwin','architecture':'arm64','license':manifest['license'],'source':manifest['source'],'manifest':manifest,'status':'installing','phase':'checking','started_at':stamp(),'verification':{'status':'not_run'}}
            tx.put('runtime_toolchain',record)
        try:
            verified=self._apple_compiler_test(manifest)
            if verified:
                record.update(**verified,status='ready',phase='ready',reason=None,finished_at=stamp())
            elif previous and previous.get('status')=='pending':
                record.update(status='pending',phase='system_consent',reason='Complete the Apple installation dialog, then check installation again. Compiler self-tests have not passed.')
            else:
                result=subprocess.run(['/usr/bin/xcode-select','--install'],capture_output=True,text=True,timeout=15)
                if result.returncode:raise ValueError('Apple installation request failed: '+(result.stderr or result.stdout)[-1500:])
                record.update(status='pending',phase='system_consent',requested_at=stamp(),reason='Approve the Apple installation dialog and its license. After macOS finishes, check installation to compile and run both C and C++ self-tests.')
        except Exception as exc:
            record.update(status='failed',phase='failed',reason=str(exc),error=str(exc),verification={'status':'failed','message':str(exc)},finished_at=stamp())
        with self.store.transaction() as tx:tx.put('runtime_toolchain',record)
        return record

    def install_manifest(self,manifest,archive=None):
        key=manifest['id'];root=self.store.path/'toolchains'/relative_path(key)
        with self.store.transaction() as tx:
            previous=tx.get('runtime_toolchain',key)
            if previous and previous['status']=='ready':return previous
            if previous and previous['status']=='installing':raise ValueError('Toolchain installation already in progress')
            record={'id':key,'name':manifest['name'],'language':manifest['language'],'status':'installing','phase':'download','downloaded_bytes':0,'started_at':stamp(),'manifest':manifest};tx.put('runtime_toolchain',record)
        root.mkdir(parents=True,exist_ok=True)
        def update(**values):
            record.update(values)
            with self.store.transaction() as tx:tx.put('runtime_toolchain',record)
        try:
            if archive is None:
                archive=root/'archive'
                with urllib.request.urlopen(manifest['url'],timeout=60) as response,archive.open('wb') as output:
                    total=int(response.headers.get('Content-Length',0));size=0
                    while chunk:=response.read(1024*1024):
                        size+=len(chunk)
                        if size>1024*1024*1024:raise ValueError('Toolchain download exceeds quota')
                        output.write(chunk);update(downloaded_bytes=size,total_bytes=total)
            archive=Path(archive);update(phase='verify',downloaded_bytes=archive.stat().st_size)
            algorithm=manifest.get('hash_algorithm','sha256');digest=hashlib.new(algorithm)
            with archive.open('rb') as stream:
                for chunk in iter(lambda:stream.read(1024*1024),b''):digest.update(chunk)
            if digest.hexdigest()!=manifest[algorithm]:raise ValueError('Toolchain checksum mismatch')
            extracted=root/'extracted';extracted.mkdir(exist_ok=True);update(phase='extract')
            if manifest['format']=='tar':
                with tarfile.open(archive) as package:
                    members=package.getmembers()
                    if sum(m.size for m in members)>3*1024**3 or len(members)>100000:raise ValueError('Toolchain archive quota exceeded')
                    package.extractall(extracted,filter='data')
            else:
                with zipfile.ZipFile(archive) as package:
                    if sum(i.file_size for i in package.infolist())>3*1024**3:raise ValueError('Toolchain archive quota exceeded')
                    for item in package.infolist():
                        name=relative_path(item.filename.rstrip('/'))
                        if (item.external_attr>>16)&0o170000==0o120000:raise ValueError('Toolchain ZIP symlink unsupported')
                        target=extracted/name
                        if item.is_dir():target.mkdir(parents=True,exist_ok=True)
                        else:
                            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(package.read(item));target.chmod((item.external_attr>>16)&0o777 or 0o644)
            homes=list(extracted.glob(manifest.get('home_glob','*')))
            if len(homes)!=1:raise ValueError('Toolchain archive home is ambiguous')
            home=homes[0];executable=home/relative_path(manifest['executable']);update(phase='self_test')
            env={**os.environ,'GRADLE_USER_HOME':str(root/'gradle-home')}
            if manifest['language'] in {'gradle','maven'}:
                with self.store.transaction() as tx:jdks=[t for t in tx.all('runtime_toolchain') if t['language']=='java' and t['status']=='ready']
                if not jdks:raise ValueError('Install a JDK before Java build tools')
                env['JAVA_HOME']=jdks[-1]['home'];env['PATH']=env['JAVA_HOME']+'/bin:'+env.get('PATH','')
            proc=subprocess.run([str(executable),*manifest.get('verify_argv',['--version'])],capture_output=True,text=True,timeout=120,env=env)
            if proc.returncode:raise ValueError('Toolchain self-test failed: '+proc.stderr[-2000:])
            update(status='ready',phase='ready',home=str(home),executable=str(executable),version=(proc.stdout+proc.stderr)[:4000],verified_digest=digest.hexdigest(),finished_at=stamp())
        except Exception as exc:update(status='failed',phase='failed',error=str(exc),finished_at=stamp())
        return record
