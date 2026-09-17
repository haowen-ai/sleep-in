#!/bin/sh
set -eu

workflow=/bootstrap/heartbeat.json
# A published workflow must be unpublished before the server CLI can replace
# it. The first boot has nothing to unpublish, so that one expected miss is
# ignored. The import and publish commands remain strict.
n8n unpublish:workflow --id=task-console-heartbeat >/dev/null 2>&1 || true
n8n import:workflow --input="$workflow"
n8n publish:workflow --id=task-console-heartbeat
