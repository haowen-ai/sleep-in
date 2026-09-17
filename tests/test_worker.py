import pytest
from fastapi.testclient import TestClient

from taskconsole.app import create_app
from taskconsole.worker import Worker


@pytest.fixture
def client(tmp_path):
    app = create_app(state_dir=tmp_path, database_url="sqlite:///" + str(tmp_path / "worker.db"))
    with TestClient(app) as value:
        token = (value.app.state.store.path / "setup-token").read_text().strip()
        response = value.post(
            "/api/setup",
            json={
                "token": token,
                "username": "owner",
                "password": "test password 12345",
                "timezone": "UTC",
                "locale": "en",
            },
        )
        assert response.status_code == 200, response.text
        value.headers["X-CSRF-Token"] = response.json()["csrf"]
        yield value


def _sample(client, sample_id):
    return next(script for script in client.get("/api/scripts").json() if script["id"] == sample_id)


def _task(client, sample_id, *, params=None):
    sample = _sample(client, sample_id)
    response = client.post(
        "/api/tasks",
        json={
            "name": "Worker test",
            "version_id": sample["default_version"],
            "params": params or {},
            "recipients": [],
            "schedule": {"kind": "manual"},
            "timezone": "UTC",
            "timeout": 10,
            "enabled": False,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def _queue(client, task_id, key="worker-test"):
    response = client.post(f"/api/tasks/{task_id}/run", headers={"Idempotency-Key": key})
    assert response.status_code == 200, response.text
    return response.json()


def test_worker_executes_sample_and_persists_logs_and_output(client):
    task = _task(client, "sample-3")
    queued = _queue(client, task["id"])

    assert Worker(client.app.state.store).run_once() is True

    finished = client.get(f"/api/executions/{queued['id']}").json()
    logs = client.get(f"/api/executions/{queued['id']}/logs").json()
    artifact = client.get(f"/api/executions/{queued['id']}/artifacts/hello.txt")
    assert finished["status"] == "succeeded"
    assert finished["artifacts"] == [{"name": "hello.txt", "size": 39}]
    assert "Created hello.txt" in logs["stdout"]
    assert artifact.status_code == 200
    assert artifact.content == b"Hello from your scheduled Python task!\n"


def test_worker_fails_execution_when_required_variable_is_missing(client):
    task = _task(client, "sample-1")
    queued = _queue(client, task["id"])
    with client.app.state.store.transaction() as tx:
        version = tx.get("version", queued["version_id"])
        version["manifest"] = {"required_variables": ["API_TOKEN"]}
        tx.put("version", version)

    assert Worker(client.app.state.store).run_once() is True

    finished = client.get(f"/api/executions/{queued['id']}").json()
    assert finished["status"] == "failed"
    assert "API_TOKEN" in finished["reason"]
    assert finished["started_at"] is not None
    assert finished["finished_at"] is not None


def test_worker_does_not_treat_runtime_environment_as_configured_variables(client):
    task = _task(client, "sample-1")
    queued = _queue(client, task["id"])
    with client.app.state.store.transaction() as tx:
        version = tx.get("version", queued["version_id"])
        version["manifest"] = {"required_variables": ["PATH"]}
        tx.put("version", version)

    Worker(client.app.state.store).run_once()

    finished = client.get(f"/api/executions/{queued['id']}").json()
    assert finished["status"] == "failed"
    assert "PATH" in finished["reason"]


def test_worker_redacts_variable_values_from_infrastructure_failure_reason(client):
    secret = "/missing-private-python-secret"
    for name,value in (("SECRET_PREFIX","/missing"),("API_TOKEN",secret)):
        response = client.post(
            "/api/admin/variables",
            json={"name": name, "scope": "instance", "value": value},
        )
        assert response.status_code == 200, response.text
    task = _task(client, "sample-1")
    queued = _queue(client, task["id"])
    with client.app.state.store.transaction() as tx:
        version = tx.get("version", queued["version_id"])
        version["runtime"]["python"] = secret
        tx.put("version", version)

    Worker(client.app.state.store).run_once()

    finished = client.get(f"/api/executions/{queued['id']}").json()
    assert finished["status"] == "failed"
    assert secret not in finished["reason"]
    assert "private-python-secret" not in finished["reason"]
    assert "[REDACTED]" in finished["reason"]


def test_worker_recover_marks_expired_active_execution_interrupted(client):
    task = _task(client, "sample-1")
    queued = _queue(client, task["id"])
    with client.app.state.store.transaction() as tx:
        run = tx.get("execution", queued["id"])
        run.update(status="running", started_at="2000-01-01T00:00:00+00:00", lease="2000-01-01T00:01:00+00:00")
        tx.put("execution", run)

    recovered = Worker(client.app.state.store).recover()

    with client.app.state.store.transaction() as tx:
        run = tx.get("execution", queued["id"])
    assert recovered == 1
    assert run["status"] == "interrupted"
    assert run["reason"] == "worker_lost"
    assert run["finished_at"] is not None


def test_worker_does_not_claim_cancelled_queued_execution(client):
    task = _task(client, "sample-1")
    queued = _queue(client, task["id"])
    cancelled = client.post(f"/api/executions/{queued['id']}/cancel").json()

    assert cancelled["status"] == "cancelled"
    assert Worker(client.app.state.store).run_once() is False
    assert client.get(f"/api/executions/{queued['id']}").json()["status"] == "cancelled"


def test_worker_uses_queued_snapshot_after_task_edit(client):
    task = _task(client, "sample-2", params={"name": "Original"})
    queued = _queue(client, task["id"])
    task["params"] = {"name": "Changed"}
    response = client.put(f"/api/tasks/{task['id']}", json=task)
    assert response.status_code == 200, response.text

    assert Worker(client.app.state.store).run_once() is True

    logs = client.get(f"/api/executions/{queued['id']}/logs").json()
    finished = client.get(f"/api/executions/{queued['id']}").json()
    assert "Hello, Original!" in logs["stdout"]
    assert "Changed" not in logs["stdout"]
    assert finished["params"]["name"] == "Original"
