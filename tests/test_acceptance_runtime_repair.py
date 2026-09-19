"""MAC-IN-05: real macOS launcher/supervisor, four owned missing components.

Opt in with SLEEP_IN_TEST_INSTALL_DIR pointing to the prepared managed fixture.
Only read that fixture. Python/npm acquisition uses explicit local transport
shims restoring genuine runtime bytes, not registry/download evidence. Node
repair uses the genuine cached archive and production hash/extraction. The
worker trial explicitly restores owned source; it does not claim auto-repair.
No browser, Login Item, power assertion, host network or shared runtime changes.
"""
import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import subprocess
import sys
import sysconfig
from types import SimpleNamespace

import httpx
import pytest

from taskconsole.local import read_json, request_stop, set_running_preference, status, write_json
from taskconsole.store import Store
from test_acceptance_local_recovery import alive, until


pytestmark = pytest.mark.skipif(
    not os.environ.get('SLEEP_IN_TEST_INSTALL_DIR') or sys.platform != 'darwin' or platform.machine() != 'arm64',
    reason='BLOCKED_ENV: read-only prepared managed Apple-silicon macOS fixture required')


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(argv, **kwargs):
    result = subprocess.run(argv, capture_output=True, text=True, timeout=150, **kwargs)
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def install_n8n(source, destination):
    """Own deletable CLI/package files; immutable dependencies only read via links."""
    modules = destination / 'node_modules'; modules.mkdir(parents=True)
    for entry in (source / 'node_modules').iterdir():
        if entry.name != 'n8n':
            (modules / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())
    package = modules / 'n8n'; package.mkdir()
    for entry in (source / 'node_modules' / 'n8n').iterdir():
        if entry.name == 'bin':
            shutil.copytree(entry, package / 'bin')
        elif entry.name == 'package.json':
            shutil.copy2(entry, package / entry.name)
        else:
            (package / entry.name).symlink_to(entry, target_is_directory=entry.is_dir())


def test_managed_python_pack_copies_required_dylib_before_bootstrap(tmp_path):
    """The shipped standalone Python must build a usable immutable Python pack."""
    from taskconsole.workflows_packs import RuntimePacks, prepare_node
    from taskconsole.workflows_runtime import run_script
    fixture = Path(os.environ['SLEEP_IN_TEST_INSTALL_DIR']).resolve()
    managed_python = fixture / 'venv/bin/python'
    store = Store(tmp_path, 'sqlite:///' + str(tmp_path / 'console.db'))
    try:
        packs = RuntimePacks(store)
        profile = packs.create({'language': 'python', 'config': {'executable': str(managed_python)}})
        built = packs.build(profile['id'])
        assert built['status'] == 'ready', built['build_log']
        private = packs.version(built['id'], True)
        library = Path(private['directory']) / 'env/lib/libpython3.12.dylib'
        assert library.is_file() and not library.is_symlink()
        assert private['manifest']['env/lib/libpython3.12.dylib'] == digest(library)
        node = prepare_node(store, {'id': 'managed', 'kind': 'python',
            'source': 'import sys\ndef main(inputs):return {"version":list(sys.version_info[:3]),"ok":True}',
            'config': {'runtime_version_id': built['id']}})
        result = run_script(node, {}, tmp_path / 'execution', tmp_path)
        assert result['status'] == 'succeeded' and result['output']['data'] == {'version': [3, 12, 11], 'ok': True}
        with library.open('ab') as handle:
            handle.write(b'owned-library-tamper')
        with pytest.raises(ValueError, match='Immutable runtime'):
            prepare_node(store, node)
    finally:
        store.engine.dispose()


@pytest.fixture(scope='module')
def managed_install(tmp_path_factory, record_testsuite_property):
    fixture = Path(os.environ['SLEEP_IN_TEST_INSTALL_DIR']).resolve()
    root = tmp_path_factory.mktemp('owned-runtime-repair')
    source = root / 'source'; source.mkdir()
    repository = Path(__file__).resolve().parents[1]
    shutil.copytree(repository / 'taskconsole', source / 'taskconsole',
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    for name in ('launch-mac.command', 'requirements.txt'):
        shutil.copy2(repository / name, source / name)
    record_testsuite_property('repair_source_sha256', json.dumps({
        str(path.relative_to(source)): digest(path)
        for path in sorted(source.rglob('*')) if path.is_file()}, sort_keys=True))
    record_testsuite_property('repair_acquisition', 'Python/npm synthetic local byte restoration; Node real cached archive SHA/extraction; all runtime execution real')
    install = root / 'installation'; install.mkdir()
    state = install / 'state'
    node_root = install / 'tools' / 'node-v24.13.0-darwin-arm64'
    node_root.parent.mkdir()
    archive = fixture / 'downloads' / 'node.tar.gz'
    assert digest(archive) == 'd595961e563fcae057d4a0fb992f175a54d97fcc4a14dc2d474d92ddeea3b9f8'
    downloads = install / 'downloads'; downloads.mkdir()
    shutil.copy2(archive, downloads / archive.name)
    command(['/usr/bin/tar', '-xzf', str(downloads / archive.name), '-C', str(node_root.parent)])
    python = install / 'venv' / 'bin' / 'python'
    managed_python = fixture / 'venv/bin/python'
    assert command([str(managed_python), '--version']).stdout.strip() == 'Python 3.12.11'
    # Fresh owned venv copies a genuine interpreter. A .pth only reads existing
    # installed dependencies; no pip/npm process can write the shared fixture.
    purelib = sysconfig.get_path('purelib')
    restorer = root / 'restore-python.py'
    restorer.write_text('import pathlib,subprocess,sys\n'
        f'root=pathlib.Path({str(install / "venv")!r})\n'
        f'subprocess.run([{str(managed_python)!r},"-m","venv","--copies","--without-pip",str(root)],check=True)\n'
        f'library=pathlib.Path({str(managed_python.resolve().parent.parent / "lib/libpython3.12.dylib")!r})\n'
        'link=root/"lib"/library.name\n'
        'if not link.exists():link.symlink_to(library)\n'
        'site=next((root/"lib").glob("python*/site-packages"))\n'
        f'(site/"fixture-dependencies.pth").write_text({purelib + chr(10)!r})\n')
    command([sys.executable, str(restorer)])
    uv = install / 'tools' / 'uv-aarch64-apple-darwin' / 'uv'; uv.parent.mkdir()
    transport = root / 'transport-events.txt'
    uv.write_text('#!' + sys.executable + '\nimport pathlib,subprocess,sys\n'
        f'with pathlib.Path({str(transport)!r}).open("a") as f:f.write("python:"+" ".join(sys.argv[1:])+"\\n")\n'
        'if sys.argv[1:3]==["python","install"]:sys.exit(0)\n'
        f'if sys.argv[1]=="venv":subprocess.run([{sys.executable!r},{str(restorer)!r}],check=True)\n'
        'else:raise SystemExit("Synthetic transport forbids pip or other writes")\n')
    uv.chmod(0o700)
    (install / 'python-requirements.sha256').write_text(digest(source / 'requirements.txt') + '\n')
    install_n8n(fixture / 'n8n', install / 'n8n')
    (install / 'n8n-ready').touch()
    # Explicit synthetic npm acquisition adapter. It copies only fixture CLI
    # bytes; the production launcher executes/validates actual Node and n8n.
    npm_restorer = root / 'restore-n8n.py'
    npm_restorer.write_text('import pathlib,sys\n' +
        'sys.path.insert(0,' + repr(str(repository / 'tests')) + ')\n' +
        'from test_acceptance_runtime_repair import install_n8n\n' +
        f'install_n8n(pathlib.Path({str(fixture / "n8n")!r}),pathlib.Path(sys.argv[1]))\n')
    def npm_transport():
        npm = node_root / 'lib/node_modules/npm/bin/npm-cli.js'
        npm.write_text('const fs=require("fs"),cp=require("child_process");\n'
            f'fs.appendFileSync({json.dumps(str(transport))},"n8n:local-byte-restoration\\n");\n'
            'const args=process.argv.slice(2);if(args[0]!=="install"||!args.includes("n8n@2.39.7"))throw Error("Unexpected acquisition");\n'
            f'cp.execFileSync({json.dumps(sys.executable)},[{json.dumps(str(npm_restorer))},args[args.indexOf("--prefix")+1]],{{stdio:"inherit"}});\n')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0)); port = sock.getsockname()[1]
    write_json(state / 'local-config.json', {'app_port': port, 'disable_power_assertion': True})
    env = {**os.environ, 'SLEEP_IN_INSTALL_DIR': str(install), 'PYTHONDONTWRITEBYTECODE': '1',
           'PYTHONPATH': str(source), 'APP_HOST': '0.0.0.0', 'SLEEP_IN_ENABLE_POWER_ASSERTION': '0'}
    for key in ('APP_STATE_DIR', 'DATABASE_URL', 'SLEEP_IN_N8N_COMMAND', 'SLEEP_IN_NODE'):
        env.pop(key, None)
    s = SimpleNamespace(root=root, source=source, install=install, state=state, python=python,
        node=node_root / 'bin/node', n8n=install / 'n8n/node_modules/n8n/bin/n8n',
        worker=source / 'taskconsole/workflows_n8n.py', port=port, env=env,
        transport=transport, npm_transport=npm_transport, runs=[])
    # These are the only shared runtime bytes we depend on directly. Their
    # hashes must be unchanged even after explicit component repair.
    shared = [archive, fixture / 'n8n/node_modules/n8n/bin/n8n', managed_python.resolve()]
    fingerprints = {path: digest(path) for path in shared}
    try:
        launch(s)
        with client(s) as http:
            template = http.get('/api/workflow-templates').json()[0]
            template.pop('id', None)
            response = http.post('/api/workflows', json=template)
            assert response.status_code == 200, response.text
            s.workflow = response.json()
            response = http.post('/api/workflows/' + s.workflow['id'] + '/publish', json={})
            assert response.status_code == 200, response.text
            s.version = response.json()['version_id']
        golden(s, 'baseline')
        stop(s)
        yield s
    finally:
        stop(s)
        assert {path: digest(path) for path in shared} == fingerprints


@contextmanager
def client(s):
    http = httpx.Client(base_url=f'http://127.0.0.1:{s.port}', timeout=15, trust_env=False)
    try:
        response = http.post('/api/login', json={'username': 'admin', 'password': 'sleepin123456'})
        assert response.status_code == 200
        user = response.json()['user']
        if hasattr(s, 'admin_id'):
            assert user['id'] == s.admin_id
        else:
            s.admin_id = user['id']
        http.headers['X-CSRF-Token'] = response.json()['csrf']
        yield http
    finally:
        http.close()


def launch(s, success=True):
    set_running_preference(s.state, True)
    result = subprocess.run(['/bin/bash', str(s.source / 'launch-mac.command'), '--login'],
        env=s.env, capture_output=True, text=True, timeout=150)
    if success:
        assert result.returncode == 0, result.stdout + result.stderr
        value = status(s.state)
        assert value['state'] == 'running' and value['components']['worker'] == 'ready', value
        assert value['components']['power'] == 'disabled-for-test' and value['assertion'] is False
        assert read_json(s.install / 'install-progress.json')['phase'] == 'ready'
        assert all(alive(pid) for pid in value['children'].values())
    else:
        assert result.returncode != 0
    return result


def stop(s):
    previous = status(s.state)
    request_stop(s.state, 'cancel')
    until(lambda: status(s.state)['state'] == 'stopped', timeout=35)
    until(lambda: all(not alive(pid) for pid in previous.get('children', {}).values()), timeout=15)


def golden(s, label):
    listener = command(['/usr/sbin/lsof', '-nP', '-iTCP:' + str(s.port), '-sTCP:LISTEN', '-Fn']).stdout
    assert 'n127.0.0.1:' + str(s.port) in listener
    assert 'n*:' not in listener and 'n[::]:' not in listener
    with client(s) as http:
        for headers in ({'Host': 'attacker.invalid'}, {'X-Forwarded-For': '127.0.0.1'}):
            response = http.get('/api/workflow-health', headers=headers)
            assert response.status_code == 403
            assert 'sleepin123456' not in response.text
        health = http.get('/api/workflow-health')
        assert health.status_code == 200 and health.json()['status'] == 'ready'
        response = http.post('/api/workflows/' + s.workflow['id'] + '/run', json={'idempotency_key': label})
        assert response.status_code == 202, response.text
        run_id = response.json()['id']
        def complete():
            response = http.get('/api/workflow-runs/' + run_id)
            assert response.status_code == 200
            value = response.json()
            return value if value['status'] in {'succeeded', 'failed', 'timed_out', 'cancelled'} and value.get('adapter_finished_at') else None
        run = until(complete, timeout=100)
        assert run['status'] == 'succeeded', run
        assert run['version_id'] == s.version and run.get('n8n_execution_id')
        assert run['nodes']['summary']['output']['data']['summary'] == {'count': 3, 'total': '30.75'}
        assert len(run['nodes']) == 3 and all(len(node['attempts']) == 1 for node in run['nodes'].values())
        assert run['artifacts']
        s.runs.append(run_id)
        assert {r['id'] for r in http.get('/api/workflow-runs').json()} == set(s.runs)
        assert http.get('/api/workflows/' + s.workflow['id']).json()['published_version_id'] == s.version


@pytest.mark.parametrize('component', ['python', 'n8n', 'node', 'worker'])
def test_mac_in05_each_missing_managed_component_repairs_and_runs_real_graph(managed_install, component):
    s = managed_install
    target = getattr(s, component)
    assert target.is_file() and not target.is_symlink()
    held = s.root / ('held-' + component)
    target.rename(held)
    assert not target.exists() and status(s.state)['state'] == 'stopped'
    previous_transport = s.transport.read_text() if s.transport.exists() else ''
    try:
        if component == 'n8n':
            s.npm_transport()
        if component == 'worker':
            result = launch(s, success=False)
            assert 'Background startup failed' in result.stderr
            diagnostics = '\n'.join(path.read_text() for path in s.state.glob('local-*.log'))
            assert 'Worker exited during startup' in diagnostics
            assert 'taskconsole.workflows_n8n' in diagnostics and 'ModuleNotFoundError' in diagnostics
            assert status(s.state)['state'] == 'stopped' and status(s.state)['components'] == {}
            with socket.socket() as probe:
                assert probe.connect_ex(('127.0.0.1', s.port)) != 0
            # Explicit repair restores ONLY this installation's worker source.
            held.rename(target)
            launch(s)
        else:
            result = launch(s)
            assert target.is_file()
            if component == 'python':
                assert 'Installing managed Python 3.12.11' in result.stdout
                assert 'python:venv --python 3.12.11' in s.transport.read_text()
            elif component == 'n8n':
                assert 'Installing pinned n8n 2.39.7' in result.stdout
                assert 'n8n:local-byte-restoration' in s.transport.read_text()
                assert command([str(s.node), str(s.n8n), '--version']).stdout.strip() == '2.39.7'
            else:
                assert digest(target) == digest(held)
                assert (s.transport.read_text() if s.transport.exists() else '') == previous_transport
                assert command([str(target), '--version']).stdout.strip() == 'v24.13.0'
        golden(s, 'repaired-' + component)
    finally:
        stop(s)
        if held.exists() and not target.exists():
            held.rename(target)
