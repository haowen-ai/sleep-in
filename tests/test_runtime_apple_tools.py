"""Mocked OS boundary only: never opens the real Apple installation dialog."""
from pathlib import Path
import subprocess
import pytest
from taskconsole.store import Store
from taskconsole.workflows_toolchains import Toolchains
import taskconsole.workflows_toolchains as module

ID='apple-command-line-tools-arm64'

@pytest.fixture
def setup(tmp_path,monkeypatch):
    monkeypatch.setattr(module.platform,'system',lambda:'Darwin')
    monkeypatch.setattr(module.platform,'machine',lambda:'arm64')
    store=Store(tmp_path/'state','sqlite:///'+str(tmp_path/'state.sqlite'))
    yield Toolchains(store),store,tmp_path
    store.engine.dispose()


def test_apple_candidate_declares_system_consent_and_listing_has_no_side_effects(setup,monkeypatch):
    tools,_,_=setup
    monkeypatch.setattr(module.subprocess,'run',lambda *a,**k:pytest.fail('Listing must not invoke OS commands'))
    candidate=next(c for c in tools.list()['candidates'] if c['id']==ID)
    assert candidate['system_consent'] is True
    assert candidate['installer']=='system_dialog' and candidate['languages']==['c','cpp']
    assert 'Apple' in candidate['license'] and candidate['source'].startswith('https://developer.apple.com/')


def test_explicit_install_requests_dialog_but_never_reports_ready(setup,monkeypatch):
    tools,_,_=setup;calls=[]
    def run(argv,**kwargs):
        calls.append(argv)
        if argv==['/usr/bin/xcode-select','-p']:return subprocess.CompletedProcess(argv,1,'','No active developer directory')
        assert argv==['/usr/bin/xcode-select','--install']
        return subprocess.CompletedProcess(argv,0,'','install requested for command line developer tools')
    monkeypatch.setattr(module.subprocess,'run',run)
    result=tools.install(ID)
    assert result['status']=='pending' and result['phase']=='system_consent'
    assert result['verification']['status']=='not_run' and result['system_consent'] is True
    assert calls.count(['/usr/bin/xcode-select','--install'])==1
    # Rechecking a pending request must not repeatedly open system dialogs.
    result=tools.install(ID)
    assert result['status']=='pending' and calls.count(['/usr/bin/xcode-select','--install'])==1


def test_failed_system_request_is_failed_and_actionable(setup,monkeypatch):
    tools,_,_=setup
    def run(argv,**kwargs):return subprocess.CompletedProcess(argv,1,'','no GUI session available')
    monkeypatch.setattr(module.subprocess,'run',run)
    result=tools.install(ID)
    assert result['status']=='failed' and 'GUI' in result['reason']
    assert result['verification']['status']!='passed'


def install_fake_compilers(root):
    developer=root/'CommandLineTools';binary=developer/'usr/bin';binary.mkdir(parents=True)
    for name in ('clang','clang++'):(binary/name).write_text('synthetic compiler fixture')
    return developer,binary


def test_ready_requires_both_real_protocol_compile_and_execute_steps(setup,monkeypatch):
    tools,store,root=setup;developer,binary=install_fake_compilers(root);calls=[]
    with store.transaction() as tx:tx.put('runtime_toolchain',{'id':ID,'status':'pending','system_consent':True})
    def run(argv,**kwargs):
        calls.append(argv)
        if argv==['/usr/bin/xcode-select','-p']:return subprocess.CompletedProcess(argv,0,str(developer)+'\n','')
        if argv[0] in [str(binary/'clang'),str(binary/'clang++')]:
            if '--version' in argv:return subprocess.CompletedProcess(argv,0,'Apple clang fixture version\n','')
            assert any(flag in argv for flag in ('-std=c11','-std=c++17'))
            output=Path(argv[argv.index('-o')+1]);output.write_text('synthetic executable')
            return subprocess.CompletedProcess(argv,0,'','')
        assert Path(argv[0]).name in {'c-selftest','cpp-selftest'}
        return subprocess.CompletedProcess(argv,0,'7\n','')
    monkeypatch.setattr(module.subprocess,'run',run)
    result=tools.install(ID)
    assert result['status']=='ready' and result['verification']['status']=='passed'
    assert result['verification']['languages']==['c','cpp']
    assert result['executables']=={'c':str(binary/'clang'),'cpp':str(binary/'clang++')}
    assert not any('--install' in argv for argv in calls)
    assert len([argv for argv in calls if '-o' in argv])==2


def test_compiler_version_without_working_linker_is_not_ready(setup,monkeypatch):
    tools,_,root=setup;developer,binary=install_fake_compilers(root)
    def run(argv,**kwargs):
        if argv==['/usr/bin/xcode-select','-p']:return subprocess.CompletedProcess(argv,0,str(developer),'')
        assert '--install' not in argv
        if '--version' in argv:return subprocess.CompletedProcess(argv,0,'Apple clang fixture','')
        return subprocess.CompletedProcess(argv,1,'','SDK or linker unavailable')
    monkeypatch.setattr(module.subprocess,'run',run)
    result=tools.install(ID)
    assert result['status']=='failed' and 'linker' in result['reason']
    assert result['verification']['status']=='failed'


def test_apple_candidate_is_not_advertised_on_linux(setup,monkeypatch):
    tools,_,_=setup;monkeypatch.setattr(module.platform,'system',lambda:'Linux')
    assert ID not in [m['id'] for m in tools.list()['candidates']]
    with pytest.raises(ValueError,match='incompatible'):tools.install(ID)


def test_ready_apple_toolchain_resolves_both_language_executables(setup,monkeypatch):
    from taskconsole.workflows_packs import RuntimePacks
    tools,store,root=setup;developer,binary=install_fake_compilers(root)
    paths={'c':str(binary/'clang'),'cpp':str(binary/'clang++')}
    for path in paths.values():Path(path).chmod(0o755)
    with store.transaction() as tx:tx.put('runtime_toolchain',{'id':ID,'status':'ready','home':str(developer/'usr'),'executables':paths})
    monkeypatch.setattr(module.subprocess,'run',lambda argv,**kwargs:subprocess.CompletedProcess(argv,0,'Apple clang fixture',''))
    for language,path in paths.items():
        resolved=RuntimePacks(store)._resolve_tools(language,{'toolchain_id':ID})
        assert resolved['compiler']==path
