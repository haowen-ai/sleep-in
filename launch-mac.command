#!/bin/bash
# Source/packaged bootstrap. No system Python, Node, Docker, sudo or shell rc edits.
set -euo pipefail
umask 077
SOURCE_ROOT="$(cd "$(dirname "$0")" && pwd)"
INSTALL_ROOT="${SLEEP_IN_INSTALL_DIR:-$HOME/Library/Application Support/Sleep In}"
STATE_ROOT="$INSTALL_ROOT/state"
ACTION="${1:---launch}"
case "$ACTION" in
  --launch|--start|--login|--status|--stop-finish|--stop-cancel|--install-only|--update) ;;
  *) echo "Unknown launch action: $ACTION" >&2; exit 1 ;;
esac
mkdir -p "$INSTALL_ROOT" "$STATE_ROOT"
cd "$SOURCE_ROOT"
export PYTHONPATH="$SOURCE_ROOT${PYTHONPATH:+:$PYTHONPATH}"
PYTHON="$INSTALL_ROOT/venv/bin/python"
case "$ACTION" in
  --update)
    if [ ! -x "$PYTHON" ]; then echo 'Install Sleep In before applying an update.' >&2; exit 1; fi
    CURRENT_APP="$(cd "$SOURCE_ROOT/../../.." && pwd)"
    exec "$PYTHON" -m taskconsole.local_update "${2:?Choose a signed update app}" --current "$CURRENT_APP" --install-root "$INSTALL_ROOT" --mode "${3:-finish}" ;;
  --status)
    if [ ! -x "$PYTHON" ]; then echo '{"state":"not_installed","assertion":false}'; exit 0; fi
    exec "$PYTHON" -m taskconsole.local --state "$STATE_ROOT" status ;;
  --stop-finish|--stop-cancel)
    if [ ! -x "$PYTHON" ]; then echo '{"state":"stopped","assertion":false}'; exit 0; fi
    exec "$PYTHON" -m taskconsole.local --state "$STATE_ROOT" stop --mode "${ACTION#--stop-}" ;;
esac
if [ -x "$PYTHON" ]; then "$PYTHON" -m taskconsole.local --state "$STATE_ROOT" check-update; fi
if [ "$(uname -s)" != Darwin ]; then echo 'Sleep In local installation requires macOS.' >&2; exit 1; fi
if [ "$(uname -m)" != arm64 ]; then echo 'This preview currently supports Apple silicon only. Intel packaging is not yet verified.' >&2; exit 1; fi
OS_MAJOR="$(sw_vers -productVersion | cut -d. -f1)"
if [ "$OS_MAJOR" -lt 13 ]; then echo 'macOS 13 or newer is required.' >&2; exit 1; fi
# Atomic mkdir serializes concurrent download/installation attempts.
LOCK_ROOT="$INSTALL_ROOT/installation.lock"
if ! mkdir "$LOCK_ROOT" 2>/dev/null; then
  # Serialize recovery too: two launchers must not both delete a stale lock.
  RECOVERY_LOCK="$INSTALL_ROOT/installation-recovery.lock"
  if ! mkdir "$RECOVERY_LOCK" 2>/dev/null; then
    echo 'Another launch is checking installation state. Please retry shortly.' >&2; exit 1
  fi
  LOCK_PID="$(cat "$LOCK_ROOT/pid" 2>/dev/null || true)"
  RECOVER=false
  case "$LOCK_PID" in
    ''|*[!0-9]*)
      LOCK_AGE=$(( $(date +%s) - $(stat -f %m "$LOCK_ROOT") ))
      if [ "$LOCK_AGE" -gt 120 ]; then RECOVER=true; fi ;;
    *) if ! kill -0 "$LOCK_PID" 2>/dev/null; then RECOVER=true; fi ;;
  esac
  if [ "$RECOVER" != true ]; then
    rmdir "$RECOVERY_LOCK"
    echo 'Installation is already running. Please wait, then open Sleep In again.' >&2; exit 1
  fi
  rm -f "$LOCK_ROOT/pid"
  rmdir "$LOCK_ROOT"
  mkdir "$LOCK_ROOT"
  echo "$$" > "$LOCK_ROOT/pid"
  rmdir "$RECOVERY_LOCK"
fi
echo "$$" > "$LOCK_ROOT/pid"
release_install_lock() { rm -f "$LOCK_ROOT/pid"; rmdir "$LOCK_ROOT" 2>/dev/null || true; }
trap release_install_lock EXIT
mkdir -p "$INSTALL_ROOT/downloads" "$INSTALL_ROOT/tools"
progress() {
  # Phase/detail values are fixed application strings, not user input.
  printf '{"phase":"%s","detail":"%s","total_bytes":%s,"download_name":"%s"}\n' "$1" "$2" "${3:-0}" "${4:-}" > "$INSTALL_ROOT/install-progress.json.new"
  mv "$INSTALL_ROOT/install-progress.json.new" "$INSTALL_ROOT/install-progress.json"
}
FREE_KB="$(df -k "$INSTALL_ROOT" | tail -1 | awk '{print $4}')"
if [ "$FREE_KB" -lt 2097152 ]; then progress failed 'At least 2 GiB of free disk space is required'; exit 1; fi
progress checking 'Checking managed runtimes'

download_verified() {
  local url="$1" destination="$2" expected="$3"
  if [ -f "$destination" ] && [ "$(shasum -a 256 "$destination" | cut -d' ' -f1)" = "$expected" ]; then return; fi
  # Reuse a completed download left by a crash before activation. Resuming it
  # would request bytes beyond EOF and may leave Retry stuck on HTTP 416.
  if [ -f "$destination.partial" ] && [ "$(shasum -a 256 "$destination.partial" | cut -d' ' -f1)" = "$expected" ]; then
    mv "$destination.partial" "$destination"
    return
  fi
  local total_size
  total_size="$(curl --silent --head --location --proto '=https' --tlsv1.2 --max-time 20 "$url" | tr -d '\r' | awk 'tolower($1)=="content-length:" {n=$2} END {print n+0}' || true)"
  progress downloading "$(basename "$destination")" "$total_size" "$(basename "$destination").partial"
  echo "Downloading $(basename "$destination") (resumes interrupted transfer)…"
  local download_status=0
  curl --fail --location --proto '=https' --tlsv1.2 --retry 3 --continue-at - --output "$destination.partial" "$url" || download_status=$?
  if [ "$download_status" -ne 0 ]; then
    case "$download_status" in
      22|33|36)
        # A server may reject a range for a complete but corrupt cached file.
        # Restart that transfer once; activation still requires the pinned hash.
        rm -f "$destination.partial"
        curl --fail --location --proto '=https' --tlsv1.2 --retry 3 --output "$destination.partial" "$url" ;;
      *) return "$download_status" ;;
    esac
  fi
  if [ "$(shasum -a 256 "$destination.partial" | cut -d' ' -f1)" != "$expected" ]; then
    rm -f "$destination.partial"
    echo 'Download integrity check failed. No downloaded code was started; choose Retry.' >&2; return 1
  fi
  mv "$destination.partial" "$destination"
}
UV="$INSTALL_ROOT/tools/uv-aarch64-apple-darwin/uv"
if [ ! -x "$UV" ]; then
  download_verified 'https://github.com/astral-sh/uv/releases/download/0.8.22/uv-aarch64-apple-darwin.tar.gz' "$INSTALL_ROOT/downloads/uv.tar.gz" '3f61099e261e449527141dbf125629fab33ad696468c8c90cebbac40185a306c'
  tar -xzf "$INSTALL_ROOT/downloads/uv.tar.gz" -C "$INSTALL_ROOT/tools"
fi
export UV_PYTHON_INSTALL_DIR="$INSTALL_ROOT/python"
export UV_CACHE_DIR="$INSTALL_ROOT/cache/uv"
export npm_config_cache="$INSTALL_ROOT/cache/npm"
if [ ! -x "$PYTHON" ]; then
  progress python 'Installing managed Python 3.12.11'
  echo 'Installing managed Python 3.12.11…'
  "$UV" python install --no-bin 3.12.11
  "$UV" venv --python 3.12.11 "$INSTALL_ROOT/venv"
fi
REQ_SHA="$(shasum -a 256 "$SOURCE_ROOT/requirements.txt" | cut -d' ' -f1)"
if [ ! -f "$INSTALL_ROOT/python-requirements.sha256" ] || [ "$(cat "$INSTALL_ROOT/python-requirements.sha256")" != "$REQ_SHA" ]; then
  progress dependencies 'Preparing Python dependencies'
  echo 'Preparing Python dependencies…'
  "$UV" pip install --python "$PYTHON" -r "$SOURCE_ROOT/requirements.txt"
  echo "$REQ_SHA" > "$INSTALL_ROOT/python-requirements.sha256"
fi
NODE_ROOT="$INSTALL_ROOT/tools/node-v24.13.0-darwin-arm64"
if [ ! -x "$NODE_ROOT/bin/node" ]; then
  download_verified 'https://nodejs.org/dist/v24.13.0/node-v24.13.0-darwin-arm64.tar.gz' "$INSTALL_ROOT/downloads/node.tar.gz" 'd595961e563fcae057d4a0fb992f175a54d97fcc4a14dc2d474d92ddeea3b9f8'
  tar -xzf "$INSTALL_ROOT/downloads/node.tar.gz" -C "$INSTALL_ROOT/tools"
fi
export PATH="$NODE_ROOT/bin:/usr/bin:/bin:/usr/sbin:/sbin"
N8N="$INSTALL_ROOT/n8n/node_modules/n8n/bin/n8n"
n8n_version_matches() {
  [ -f "$1" ] && [ "$("$NODE_ROOT/bin/node" "$1" --version 2>/dev/null)" = '2.39.7' ]
}
if [ ! -f "$INSTALL_ROOT/n8n-ready" ] || ! n8n_version_matches "$N8N"; then
  rm -f "$INSTALL_ROOT/n8n-ready"
  progress n8n 'Installing n8n 2.39.7; this may take several minutes'
  echo 'Installing pinned n8n 2.39.7 (first download can take several minutes)…'
  # Fresh staging avoids npm considering damaged files already up-to-date.
  # Existing files remain in place unless the replacement passes its self-check.
  N8N_PREPARE="$(mktemp -d "$INSTALL_ROOT/n8n-prepare.XXXXXX")"
  "$NODE_ROOT/bin/node" "$NODE_ROOT/lib/node_modules/npm/bin/npm-cli.js" install --prefix "$N8N_PREPARE" --omit=dev --legacy-peer-deps --no-audit --no-fund n8n@2.39.7
  if ! n8n_version_matches "$N8N_PREPARE/node_modules/n8n/bin/n8n"; then
    echo 'Prepared n8n failed its version self-check; previous installation was not replaced.' >&2
    exit 1
  fi
  if [ -e "$INSTALL_ROOT/n8n" ]; then
    mv "$INSTALL_ROOT/n8n" "$INSTALL_ROOT/n8n-previous-$(date +%s)-$$"
  fi
  mv "$N8N_PREPARE" "$INSTALL_ROOT/n8n"
  touch "$INSTALL_ROOT/n8n-ready"
fi
"$PYTHON" - "$STATE_ROOT" "$NODE_ROOT/bin/node" "$N8N" <<'PY'
import sys
from pathlib import Path
from taskconsole.local import read_json,write_json
path=Path(sys.argv[1])/'local-config.json'
config=read_json(path,{})
config.update(n8n_command=sys.argv[2:],app_port=config.get('app_port',8765))
write_json(path,config)
PY
progress ready 'Managed runtimes verified'
release_install_lock
trap - EXIT
case "$ACTION" in
  --install-only) echo 'Managed runtimes prepared. No service started.' ;;
  --login) exec "$PYTHON" -m taskconsole.local --state "$STATE_ROOT" start ;;
  --start) exec "$PYTHON" -m taskconsole.local --state "$STATE_ROOT" start --explicit --open ;;
  --launch) exec "$PYTHON" -m taskconsole.local --state "$STATE_ROOT" start --open ;;
  *) echo "Unknown launch action: $ACTION" >&2; exit 1 ;;
esac
