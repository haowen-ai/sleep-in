"""Build and execute trusted-administrator Python task bundles."""

from __future__ import annotations

import ast
import codecs
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import venv
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable


MAX_UPLOAD_BYTES = 20 * 1024 * 1024
MAX_SOURCE_BYTES = 1 * 1024 * 1024
MAX_EXPANDED_BYTES = 100 * 1024 * 1024
MAX_ARCHIVE_FILES = 2_000
MAX_LOG_BYTES = 20 * 1024 * 1024
MAX_ARTIFACT_BYTES = 100 * 1024 * 1024
MAX_ARTIFACT_FILES = 100
TERMINATION_GRACE_SECONDS = 30

_SECRET_NAMES = {
    ".env",
    ".env.local",
    "id_rsa",
    "id_dsa",
    "id_ecdsa",
    "id_ed25519",
    "credentials.json",
    "secrets.json",
}
_PINNED_REQUIREMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*(?:\[[A-Za-z0-9_,.-]+\])?==[^\s;]+(?:\s*;.*)?$")


def inspect_source(source: str) -> dict:
    """Classify source without importing or evaluating it."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError) as exc:
        raise ValueError("source must be valid Python") from exc
    main_nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "main"]
    if len(main_nodes) > 1:
        raise ValueError("source must define main only once")
    if any(isinstance(node, ast.AsyncFunctionDef) for node in main_nodes):
        raise ValueError("main must be a synchronous function")
    if main_nodes:
        arguments = main_nodes[0].args
        positional = [*arguments.posonlyargs, *arguments.args]
        if (
            len(positional) != 1
            or positional[0].arg != "params"
            or arguments.defaults
            or arguments.vararg
            or arguments.kwarg
            or arguments.kwonlyargs
        ):
            raise ValueError("main must have the signature main(params)")
    has_main = bool(main_nodes)
    return {"mode": "function" if has_main else "script"}


def _safe_archive_name(name: str) -> PurePosixPath:
    if not name or "\\" in name:
        raise ValueError("unsafe archive path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts:
        raise ValueError("unsafe archive path")
    return path


def _is_ignored_archive_path(path: PurePosixPath) -> bool:
    lowered = [part.lower() for part in path.parts]
    return (
        any(part in {"__macosx", ".git", "venv", ".venv", "__pycache__"} for part in lowered)
        or any(part == ".ds_store" or part.startswith("._") for part in lowered)
    )


def _is_secret_archive_path(path: PurePosixPath) -> bool:
    for part in path.parts:
        lowered = part.lower()
        if lowered == ".env.example":
            continue
        if (
            lowered in _SECRET_NAMES
            or lowered.startswith(".env.")
            or Path(lowered).suffix in {".key", ".pem", ".p12", ".pfx"}
        ):
            return True
    return False


def _requirements_text(source_dir: Path) -> str:
    path = source_dir / "requirements.txt"
    if not path.exists():
        return ""
    if path.is_symlink() or not path.is_file():
        raise ValueError("requirements.txt must be a regular file")
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("requirements.txt must be UTF-8") from exc


def prepare_bundle(data: bytes, filename: str, dest: Path) -> dict:
    """Validate and expand one Python file or ZIP project into *dest*."""
    if len(data) > MAX_UPLOAD_BYTES:
        raise ValueError("upload exceeds 20 MiB")
    dest = Path(dest)
    if dest.exists() and any(dest.iterdir()):
        raise ValueError("destination must be empty")

    suffix = Path(filename).suffix.lower()
    if suffix == ".py":
        if len(data) > MAX_SOURCE_BYTES:
            raise ValueError("Python source exceeds 1 MiB")
        try:
            source = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("source must be UTF-8") from exc
        result = inspect_source(source)
        dest.mkdir(parents=True, exist_ok=True)
        (dest / "main.py").write_bytes(data)
        return {**result, "requirements": ""}
    if suffix != ".zip":
        raise ValueError("upload must be a .py or .zip file")

    try:
        with zipfile.ZipFile(__import__("io").BytesIO(data)) as archive:
            infos = archive.infolist()
            accepted: list[tuple[zipfile.ZipInfo, PurePosixPath]] = []
            ignored: list[str] = []
            seen: set[PurePosixPath] = set()
            for info in infos:
                path = _safe_archive_name(info.filename)
                if path in seen:
                    raise ValueError("archive contains duplicate paths")
                seen.add(path)
                if info.flag_bits & 0x1:
                    raise ValueError("encrypted archives are not accepted")
                mode = (info.external_attr >> 16) & 0xFFFF
                if mode and not (info.is_dir() or (mode & 0o170000) in (0, 0o100000, 0o040000)):
                    raise ValueError("archive links and special files are not accepted")
                if info.is_dir():
                    continue
                if _is_ignored_archive_path(path):
                    ignored.append(info.filename)
                    continue
                if _is_secret_archive_path(path):
                    raise ValueError("secret files are not accepted")
                accepted.append((info, path))
            paths = [path for _, path in accepted]
            if PurePosixPath("main.py") not in paths:
                roots = {path.parts[0] for path in paths if len(path.parts) > 1}
                if len(roots) != 1 or any(len(path.parts) < 2 for path in paths):
                    raise ValueError("ZIP must contain main.py at its root or in one outer folder")
                accepted = [(info, PurePosixPath(*path.parts[1:])) for info, path in accepted]
                paths = [path for _, path in accepted]
            if PurePosixPath("main.py") not in paths:
                raise ValueError("ZIP must contain main.py at its root or in one outer folder")
            if len(set(paths)) != len(paths):
                raise ValueError("archive contains duplicate paths after removing its outer folder")
            file_infos = [info for info, _ in accepted]
            if len(file_infos) > MAX_ARCHIVE_FILES:
                raise ValueError("archive has too many files")
            if sum(info.file_size for info in file_infos) > MAX_EXPANDED_BYTES:
                raise ValueError("archive expanded size is too large")
            entry_by_path = {path: info for info, path in accepted}
            archive.read(entry_by_path[PurePosixPath("main.py")]).decode("utf-8")
            requirements_info = entry_by_path.get(PurePosixPath("requirements.txt"))
            requirements = archive.read(requirements_info).decode("utf-8") if requirements_info else ""
            dest.mkdir(parents=True, exist_ok=True)
            for info, path in accepted:
                target = dest.joinpath(*path.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info) as source_file, target.open("wb") as target_file:
                    shutil.copyfileobj(source_file, target_file)
    except (zipfile.BadZipFile, UnicodeDecodeError) as exc:
        raise ValueError("invalid ZIP or non-UTF-8 entry file") from exc
    result = {"mode": "script", "requirements": requirements}
    if ignored:
        result["ignored"] = sorted(ignored)
    return result


def _validate_locked_requirements(text: str) -> None:
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if not _PINNED_REQUIREMENT.fullmatch(line):
            raise ValueError("requirements must use pinned package==version entries")


def build_environment(source_dir: Path, runtime_dir: Path) -> dict:
    """Create a virtual environment, install pinned dependencies, and freeze it."""
    source_dir, runtime_dir = Path(source_dir), Path(runtime_dir)
    requirements = _requirements_text(source_dir)
    _validate_locked_requirements(requirements)
    if not requirements.strip():
        return {"python": sys.executable, "freeze": "", "log": "Standard library environment"}
    runtime_dir.mkdir(parents=True, exist_ok=True)
    venv_dir = runtime_dir / "venv"
    venv.EnvBuilder(with_pip=True, clear=True).create(venv_dir)
    scripts = "Scripts" if os.name == "nt" else "bin"
    python = venv_dir / scripts / ("python.exe" if os.name == "nt" else "python")
    logs: list[str] = []
    build_home = runtime_dir / "home"
    build_home.mkdir(mode=0o700)
    build_env = {
        "HOME": str(build_home),
        "PATH": os.defpath,
        "LANG": "C.UTF-8",
        "PYTHONIOENCODING": "utf-8",
        "PYTHONNOUSERSITE": "1",
        "PIP_CONFIG_FILE": os.devnull,
    }
    if requirements.strip():
        completed = subprocess.run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(source_dir / "requirements.txt")],
            capture_output=True,
            text=True,
            timeout=600,
            env=build_env,
        )
        logs.extend((completed.stdout, completed.stderr))
        if completed.returncode:
            raise RuntimeError("dependency installation failed:\n" + "".join(logs))
    frozen = subprocess.run(
        [str(python), "-m", "pip", "freeze", "--all"], capture_output=True, text=True, timeout=120, env=build_env
    )
    logs.extend((frozen.stdout, frozen.stderr))
    if frozen.returncode:
        raise RuntimeError("dependency freeze failed:\n" + "".join(logs))
    return {"python": str(python), "freeze": frozen.stdout, "log": "".join(logs)}


def _terminate_group(process: subprocess.Popen) -> None:
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
        deadline = time.monotonic() + TERMINATION_GRACE_SECONDS
        while time.monotonic() < deadline:
            if process.poll() is None:
                try:
                    process.wait(timeout=min(0.1, max(0.01, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    continue
            if os.name != "posix":
                return
            try:
                os.killpg(process.pid, 0)
            except (ProcessLookupError, PermissionError):
                return
            time.sleep(0.05)
        raise subprocess.TimeoutExpired(process.args, TERMINATION_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
        process.wait()
    except (ProcessLookupError, PermissionError):
        pass


def _collect_artifacts(output_dir: Path) -> tuple[list[dict], str | None]:
    artifacts: list[dict] = []
    total = 0
    reason = None
    for path in output_dir.rglob("*"):
        if path.is_symlink():
            reason = reason or "unsafe artifacts"
            continue
        if path.is_file():
            relative = path.relative_to(output_dir).as_posix()
            size = path.stat().st_size
            if len(artifacts) >= MAX_ARTIFACT_FILES or total + size > MAX_ARTIFACT_BYTES:
                reason = reason or "artifact limits exceeded"
                continue
            artifacts.append({"name": relative, "size": size})
            total += size
    return sorted(artifacts, key=lambda item: item["name"]), reason


def execute(
    source_dir: Path,
    mode: str,
    params: dict,
    output_dir: Path,
    python: str,
    timeout: int,
    cancel: Callable[[], bool],
    log: Callable[[str, str], None],
    env: dict,
) -> dict:
    """Execute the bundle once in a new process group and validate its outputs."""
    source_dir, output_dir = Path(source_dir), Path(output_dir)
    if mode not in {"function", "script"}:
        raise ValueError("mode must be function or script")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    output_dir.mkdir(parents=True, exist_ok=True)
    entry = Path(__file__).with_name("entry.py")
    with tempfile.TemporaryDirectory(prefix="taskconsole-run-") as temp_name:
        temp = Path(temp_name)
        params_file = temp / "params.json"
        params_file.write_text(json.dumps(params, ensure_ascii=False), encoding="utf-8")
        child_env = {str(key): str(value) for key, value in env.items()}
        child_env.update(
            {
                "TASK_PARAMS_FILE": str(params_file),
                "TASK_OUTPUT_DIR": str(output_dir.resolve()),
                "PYTHONIOENCODING": "utf-8",
                "PYTHONUNBUFFERED": "1",
            }
        )
        process = subprocess.Popen(
            [str(python), str(entry), str((source_dir / "main.py").resolve()), mode],
            cwd=source_dir,
            env=child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=(os.name == "posix"),
        )
        delivered = 0
        logs_truncated = False
        delivered_lock = threading.Lock()

        def forward(stream_name: str, pipe) -> None:
            nonlocal delivered, logs_truncated
            decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
            while chunk := pipe.read1(4096):
                with delivered_lock:
                    remaining = MAX_LOG_BYTES - delivered
                    if remaining <= 0:
                        logs_truncated = True
                        continue
                    accepted = chunk[:remaining]
                    if len(accepted) < len(chunk):
                        logs_truncated = True
                    delivered += len(accepted)
                    text = decoder.decode(accepted, final=False)
                if text:
                    log(stream_name, text)
            final = decoder.decode(b"", final=True)
            if final:
                log(stream_name, final)

        threads = [
            threading.Thread(target=forward, args=("stdout", process.stdout), daemon=True),
            threading.Thread(target=forward, args=("stderr", process.stderr), daemon=True),
        ]
        for thread in threads:
            thread.start()
        started = time.monotonic()
        reason = None
        while process.poll() is None:
            if cancel():
                reason = "cancelled"
                _terminate_group(process)
                break
            if time.monotonic() - started >= timeout:
                reason = "timeout"
                _terminate_group(process)
                break
            time.sleep(0.05)
        # A task may spawn children which outlive the entry process and keep its
        # pipes open. Close the whole process group before joining log readers.
        _terminate_group(process)
        for thread in threads:
            thread.join()
        exit_code = process.returncode

    artifacts, artifact_error = _collect_artifacts(output_dir)
    if reason == "cancelled":
        status = "cancelled"
    elif reason == "timeout":
        status = "timed_out"
    else:
        status = "succeeded" if exit_code == 0 else "failed"
        reason = None if exit_code == 0 else "process exited with an error"
    return {
        "status": status,
        "exit_code": exit_code,
        "reason": reason,
        "artifacts": artifacts,
        "artifact_reason": artifact_error,
        "logs_truncated": logs_truncated,
    }
