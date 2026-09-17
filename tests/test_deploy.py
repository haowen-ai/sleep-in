from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_heartbeat_workflow_has_stable_identity_and_reads_secret_file() -> None:
    workflow = json.loads((ROOT / "deploy/n8n/heartbeat.json").read_text())

    assert workflow["id"] == "task-console-heartbeat"
    assert workflow["name"] == "Task Console Dispatch Heartbeat"
    types = {node["type"] for node in workflow["nodes"]}
    assert "n8n-nodes-base.scheduleTrigger" in types
    assert "n8n-nodes-base.readWriteFile" in types
    assert "n8n-nodes-base.extractFromFile" in types
    request = next(node for node in workflow["nodes"] if node["type"] == "n8n-nodes-base.httpRequest")
    encoded = json.dumps(request)
    assert "http://web:8080/internal/tick" in encoded
    assert "X-Dispatch-Token" in encoded
    assert "$json.data.trim()" in encoded


def test_n8n_bootstrap_uses_supported_import_and_publish_commands() -> None:
    script = (ROOT / "deploy/n8n/bootstrap.sh").read_text()

    assert "n8n unpublish:workflow --id=task-console-heartbeat" in script
    assert "n8n import:workflow --input=" in script
    assert "n8n publish:workflow --id=task-console-heartbeat" in script
    assert "update:workflow" not in script
    assert "api" not in script.lower()


def test_compose_keeps_n8n_private_and_orders_initialization() -> None:
    compose = (ROOT / "compose.yaml").read_text()

    n8n_section = compose.split("  n8n:\n", 1)[1].split("\n  ", 1)[0]
    assert "ports:" not in n8n_section
    assert "n8n-init:\n" in compose
    assert "condition: service_completed_successfully" in compose
    assert "127.0.0.1:8080:8080" in compose
    assert "docker.sock" not in compose
    assert "/state" in compose


def test_compose_smoke_proves_scheduled_sample_output() -> None:
    smoke = (ROOT / "deploy/compose-smoke.sh").read_text()

    assert "/api/setup" in smoke
    assert "sample-3" in smoke
    assert '"kind": "interval"' in smoke
    assert "/api/executions" in smoke
    assert "hello.txt" in smoke


def test_images_are_pinned_and_release_does_not_publish_latest() -> None:
    compose = (ROOT / "compose.yaml").read_text()
    release = (ROOT / ".github/workflows/release.yml").read_text()

    assert "postgres:17.6-alpine3.22" in compose
    assert "n8nio/n8n:2.39.7" in compose
    assert "busybox:1.37.0-uclibc" in compose
    assert 'tags: ["v*"]' in release
    assert "latest" not in release.lower()


def test_dockerfile_runs_application_as_non_root() -> None:
    dockerfile = (ROOT / "Dockerfile").read_text()

    assert "FROM python:3.12.11-slim-bookworm" in dockerfile
    assert "USER 1000:1000" in dockerfile
    assert 'CMD ["python", "-m", "taskconsole", "serve"]' in dockerfile
