import io
import os
import stat
import sys
import time
import zipfile
from pathlib import Path

import pytest

from taskconsole.runtime import (
    build_environment,
    execute,
    inspect_source,
    prepare_bundle,
)


PYTHON = os.environ.get("PYTHON", os.sys.executable)


def _zip(entries: dict[str, bytes], symlinks: set[str] = set()) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        for name, data in entries.items():
            info = zipfile.ZipInfo(name)
            if name in symlinks:
                info.create_system = 3
                info.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(info, data)
    return output.getvalue()


def _run(tmp_path: Path, source: str, *, mode=None, timeout=5, cancel=lambda: False, env=None):
    source_dir = tmp_path / "source"
    output_dir = tmp_path / "output"
    source_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (source_dir / "main.py").write_text(source, encoding="utf-8")
    messages = []
    result = execute(
        source_dir,
        mode or inspect_source(source)["mode"],
        {"name": "世界"},
        output_dir,
        PYTHON,
        timeout,
        cancel,
        lambda stream, text: messages.append((stream, text)),
        env or {"TASK_RUN_ID": "run-1"},
    )
    return result, messages, output_dir


def test_inspect_source_uses_ast_without_executing_top_level_code(tmp_path):
    marker = tmp_path / "executed"
    source = f"from pathlib import Path\nPath({str(marker)!r}).write_text('bad')\ndef main(params):\n    pass\n"

    assert inspect_source(source) == {"mode": "function"}
    assert not marker.exists()
    assert inspect_source("print('plain script')") == {"mode": "script"}


def test_inspect_source_rejects_invalid_python():
    with pytest.raises(ValueError, match="valid Python"):
        inspect_source("def broken(:")


def test_prepare_single_python_and_safe_zip(tmp_path):
    single = prepare_bundle(b"def main(params):\n    return params\n", "job.py", tmp_path / "single")
    zipped = prepare_bundle(
        _zip({"main.py": b"print('zip')\n", "requirements.txt": b"urllib3==2.5.0\n"}),
        "job.zip",
        tmp_path / "zipped",
    )

    assert single == {"mode": "function", "requirements": ""}
    assert (tmp_path / "single" / "main.py").is_file()
    assert zipped == {"mode": "script", "requirements": "urllib3==2.5.0\n"}
    assert (tmp_path / "zipped" / "main.py").is_file()


def test_prepare_python_source_has_one_mib_limit(tmp_path, monkeypatch):
    monkeypatch.setattr("taskconsole.runtime.MAX_SOURCE_BYTES", 3)
    with pytest.raises(ValueError, match="1 MiB"):
        prepare_bundle(b"pass", "large.py", tmp_path / "large")


def test_prepare_zip_accepts_one_outer_folder_and_always_uses_script_mode(tmp_path):
    result = prepare_bundle(
        _zip({"project/main.py": b"def main(params):\n    raise AssertionError('must not be injected')\n"}),
        "project.zip",
        tmp_path / "project",
    )

    assert result["mode"] == "script"
    assert (tmp_path / "project" / "main.py").is_file()


def test_prepare_zip_skips_platform_and_environment_junk_with_summary(tmp_path):
    payload = _zip(
        {
            "main.py": b"pass\n",
            ".DS_Store": b"junk",
            "__MACOSX/._main.py": b"junk",
            ".git/config": b"junk",
            "venv/bin/python": b"junk",
        }
    )

    result = prepare_bundle(payload, "project.zip", tmp_path / "project")

    assert result["ignored"] == [".DS_Store", ".git/config", "__MACOSX/._main.py", "venv/bin/python"]
    assert sorted(path.name for path in (tmp_path / "project").iterdir()) == ["main.py"]


def test_prepare_zip_allows_env_example_documentation(tmp_path):
    result = prepare_bundle(
        _zip({"main.py": b"pass\n", ".env.example": b"API_TOKEN=replace-me\n"}),
        "project.zip",
        tmp_path / "project",
    )

    assert result["mode"] == "script"
    assert (tmp_path / "project" / ".env.example").is_file()


@pytest.mark.parametrize(
    "payload",
    [
        _zip({"../escape.py": b"pass", "main.py": b"pass"}),
        _zip({"main.py": b"pass", "link": b"main.py"}, {"link"}),
        _zip({"main.py": b"pass", ".env": b"TOKEN=secret"}),
        _zip({"main.py": b"pass", ".env.production": b"TOKEN=secret"}),
        _zip({"main.py": b"pass", "id_rsa": b"private"}),
        _zip({"main.py": b"pass", "server.key": b"private"}),
    ],
)
def test_prepare_bundle_rejects_traversal_symlinks_and_secret_files(tmp_path, payload):
    with pytest.raises(ValueError):
        prepare_bundle(payload, "unsafe.zip", tmp_path / "bundle")


def test_prepare_bundle_requires_root_main_and_enforces_archive_limits(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="main.py"):
        prepare_bundle(_zip({"first/main.py": b"pass", "second/code.py": b"pass"}), "nested.zip", tmp_path / "nested")

    monkeypatch.setattr("taskconsole.runtime.MAX_ARCHIVE_FILES", 1)
    with pytest.raises(ValueError, match="too many"):
        prepare_bundle(_zip({"main.py": b"pass", "extra.py": b"pass"}), "many.zip", tmp_path / "many")

    monkeypatch.setattr("taskconsole.runtime.MAX_EXPANDED_BYTES", 3)
    with pytest.raises(ValueError, match="expanded"):
        prepare_bundle(_zip({"main.py": b"pass"}), "large.zip", tmp_path / "large")

    monkeypatch.setattr("taskconsole.runtime.MAX_UPLOAD_BYTES", 3)
    with pytest.raises(ValueError, match="20 MiB"):
        prepare_bundle(b"pass", "large.py", tmp_path / "upload")


def test_build_environment_creates_isolated_python_and_freeze(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "requirements.txt").write_text("", encoding="utf-8")

    result = build_environment(source, tmp_path / "runtime")

    assert Path(result["python"]).resolve() == Path(sys.executable).resolve()
    assert result["freeze"] == ""
    assert result["log"] == "Standard library environment"


def test_build_environment_rejects_unlocked_requirements(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "requirements.txt").write_text("requests>=2\n", encoding="utf-8")

    with pytest.raises(ValueError, match="pinned"):
        build_environment(source, tmp_path / "runtime")


def test_build_environment_pip_is_bounded_and_does_not_inherit_secrets(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "requirements.txt").write_text("example-package==1.2.3\n", encoding="utf-8")
    monkeypatch.setenv("DATABASE_URL", "secret")
    calls = []

    def fake_create(self, path):
        python = Path(path) / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir(parents=True)
        python.write_text("")

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return type("Result", (), {"returncode": 0, "stdout": "example-package==1.2.3\n", "stderr": ""})()

    monkeypatch.setattr("taskconsole.runtime.venv.EnvBuilder.create", fake_create)
    monkeypatch.setattr("taskconsole.runtime.subprocess.run", fake_run)

    build_environment(source, tmp_path / "runtime")

    assert len(calls) == 2
    assert all(call[1]["timeout"] > 0 for call in calls)
    assert all("DATABASE_URL" not in call[1]["env"] for call in calls)


def test_execute_calls_main_exactly_once_and_streams_unicode(tmp_path):
    source = """\
from pathlib import Path
import os
def main(params):
    output = Path(os.environ['TASK_OUTPUT_DIR'])
    count = output / 'count.txt'
    count.write_text(str(int(count.read_text()) + 1) if count.exists() else '1')
    print('hello ' + params['name'])
"""

    result, messages, output = _run(tmp_path, source)

    assert result["status"] == "succeeded"
    assert result["exit_code"] == 0
    assert (output / "count.txt").read_text() == "1"
    assert "hello 世界" in "".join(text for _, text in messages)
    assert result["artifacts"] == [{"name": "count.txt", "size": 1}]


def test_execute_function_does_not_run_main_guard_before_injected_call(tmp_path):
    source = """\
from pathlib import Path
import os
def main(params):
    output = Path(os.environ['TASK_OUTPUT_DIR'], 'calls.txt')
    output.write_text(output.read_text() + 'x' if output.exists() else 'x')
if __name__ == '__main__':
    main({})
"""

    result, _, output = _run(tmp_path, source)

    assert result["status"] == "succeeded"
    assert (output / "calls.txt").read_text() == "x"


def test_execute_script_top_level_and_zip_entry_run_once(tmp_path):
    result, _, output = _run(
        tmp_path,
        "from pathlib import Path\nimport os\nPath(os.environ['TASK_OUTPUT_DIR'], 'ran.txt').write_text('once')\n",
    )

    assert result["status"] == "succeeded"
    assert (output / "ran.txt").read_text() == "once"


def test_inspect_source_rejects_async_or_invalid_main_contract():
    for source in (
        "async def main(params):\n    pass\n",
        "def main():\n    pass\n",
        "def main(first, second):\n    pass\n",
        "def main(params='default'):\n    pass\n",
        "def main(params):\n    pass\ndef main(params):\n    pass\n",
    ):
        with pytest.raises(ValueError, match="main"):
            inspect_source(source)


def test_execute_passes_params_file_and_does_not_inherit_parent_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://secret")
    source = """\
import json, os
from pathlib import Path
def main(params):
    assert json.loads(Path(os.environ['TASK_PARAMS_FILE']).read_text()) == params
    assert 'DATABASE_URL' not in os.environ
    assert os.environ['TASK_RUN_ID'] == 'run-1'
"""

    result, _, _ = _run(tmp_path, source)

    assert result["status"] == "succeeded"


def test_execute_times_out_and_kills_the_process_group(tmp_path):
    source = """\
import os, subprocess, sys, time
from pathlib import Path
def main(params):
    marker = Path(os.environ['TASK_OUTPUT_DIR'], 'child-lived.txt')
    subprocess.Popen([sys.executable, '-c', f'import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).write_text("bad")'])
    time.sleep(20)
"""

    result, _, output = _run(tmp_path, source, timeout=1)
    time.sleep(1.2)

    assert result["status"] == "timed_out"
    assert result["reason"] == "timeout"
    assert not (output / "child-lived.txt").exists()


def test_execute_terminates_background_children_after_parent_exits(tmp_path):
    source = """\
import os, subprocess, sys
from pathlib import Path
marker = Path(os.environ['TASK_OUTPUT_DIR'], 'child-lived.txt')
subprocess.Popen([sys.executable, '-c', f'import time; from pathlib import Path; time.sleep(1); Path({str(marker)!r}).write_text("bad")'])
"""

    result, _, output = _run(tmp_path, source)
    time.sleep(1.2)

    assert result["status"] == "succeeded"
    assert not (output / "child-lived.txt").exists()


def test_execute_honors_cancellation(tmp_path):
    started = time.monotonic()
    result, _, _ = _run(
        tmp_path,
        "import time\ndef main(params):\n    time.sleep(20)\n",
        cancel=lambda: time.monotonic() - started > 0.2,
    )

    assert result["status"] == "cancelled"
    assert result["reason"] == "cancelled"


def test_execute_rejects_symlink_and_excess_outputs(tmp_path, monkeypatch):
    result, _, _ = _run(
        tmp_path,
        "import os\nfrom pathlib import Path\ndef main(params):\n    Path(os.environ['TASK_OUTPUT_DIR'], 'link').symlink_to('/etc/hosts')\n",
    )
    assert result["status"] == "succeeded"
    assert result["artifact_reason"] == "unsafe artifacts"
    assert result["artifacts"] == []

    monkeypatch.setattr("taskconsole.runtime.MAX_ARTIFACT_FILES", 1)
    result, _, _ = _run(
        tmp_path / "second",
        "import os\nfrom pathlib import Path\ndef main(params):\n    p=Path(os.environ['TASK_OUTPUT_DIR']); (p/'a').write_text('a'); (p/'b').write_text('b')\n",
    )
    assert result["status"] == "succeeded"
    assert result["artifact_reason"] == "artifact limits exceeded"
    # Filesystem traversal order differs between APFS and Linux. The contract
    # retains a valid file within the cap, without promising which one wins.
    assert len(result["artifacts"]) == 1
    assert result["artifacts"][0] in ({"name": "a", "size": 1}, {"name": "b", "size": 1})


def test_execute_caps_forwarded_logs(tmp_path, monkeypatch):
    monkeypatch.setattr("taskconsole.runtime.MAX_LOG_BYTES", 32)
    result, messages, _ = _run(tmp_path, "def main(params):\n    print('x' * 1000)\n")

    assert result["status"] == "succeeded"
    assert sum(len(text.encode("utf-8")) for _, text in messages) <= 32
