from fastapi.testclient import TestClient

from taskconsole.app import create_app


def authenticated_client(tmp_path):
    app = create_app(state_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'review.db'}")
    client = TestClient(app)
    token = (app.state.store.path / "setup-token").read_text().strip()
    response = client.post(
        "/api/setup",
        json={
            "token": token,
            "username": "owner",
            "password": "review password 123",
            "timezone": "UTC",
            "locale": "en",
        },
    )
    assert response.status_code == 200
    client.headers["X-CSRF-Token"] = response.json()["csrf"]
    return client


def create_task(client):
    script = client.get("/api/scripts").json()[0]
    response = client.post(
        "/api/tasks",
        json={
            "name": "Review task",
            "version_id": script["default_version"],
            "params": {},
            "recipients": [],
            "schedule": {"kind": "manual"},
            "timezone": "UTC",
            "timeout": 60,
            "enabled": False,
        },
    )
    assert response.status_code == 200, response.text
    return response.json()


def assert_public_run(run):
    forbidden = {"runtime", "lease", "worker_id", "script_id", "revision", "actor"}
    assert forbidden.isdisjoint(run)
    assert {"id", "task_id", "task_name", "status", "trigger", "version_id"} <= run.keys()


def test_execution_responses_never_expose_worker_or_runtime_fields(tmp_path):
    client = authenticated_client(tmp_path)
    task = create_task(client)
    response = client.post(
        f"/api/tasks/{task['id']}/run", headers={"Idempotency-Key": "review-run"}
    )
    assert response.status_code == 200
    run = response.json()
    assert_public_run(run)

    with client.app.state.store.transaction() as tx:
        stored = tx.get("execution", run["id"])
        stored.update(worker_id="private-worker", lease="2099-01-01T00:00:00+00:00")
        tx.put("execution", stored)

    assert_public_run(client.get("/api/executions").json()["items"][0])
    assert_public_run(client.get(f"/api/executions/{run['id']}").json())
    assert_public_run(client.post(f"/api/executions/{run['id']}/cancel").json())


def test_restoring_task_with_retired_version_is_rejected(tmp_path):
    client = authenticated_client(tmp_path)
    task = create_task(client)
    script_id = task["script_id"]
    version_id = task["version_id"]
    assert client.post(f"/api/tasks/{task['id']}/archive", json={"archived": True}).status_code == 200
    assert client.post(f"/api/scripts/{script_id}/default", json={"version_id": version_id}).status_code == 200

    with client.app.state.store.transaction() as tx:
        version = tx.get("version", version_id)
        version["status"] = "retired"
        tx.put("version", version)

    response = client.post(f"/api/tasks/{task['id']}/archive", json={"archived": False})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "retired_version"


def test_default_version_change_is_audited(tmp_path):
    client = authenticated_client(tmp_path)
    script = client.get("/api/scripts").json()[0]
    response = client.post(
        f"/api/scripts/{script['id']}/default",
        json={"version_id": script["default_version"]},
    )
    assert response.status_code == 200
    events = client.get("/api/admin/audit").json()
    assert any(
        event["action"] == "script.default" and event["target"] == script["default_version"]
        for event in events
    )


def test_invalid_shapes_return_structured_400_or_422_never_500(tmp_path):
    client = authenticated_client(tmp_path)
    script = client.get("/api/scripts").json()[0]
    bad_script = client.post("/api/scripts", json={"name": [], "source": "print(1)", "manifest": {}})
    assert bad_script.status_code == 400
    assert bad_script.json()["detail"]["code"] == "invalid"

    bad_cron = client.post(
        "/api/tasks",
        json={
            "name": "Bad cron",
            "version_id": script["default_version"],
            "params": {},
            "recipients": [],
            "schedule": {"kind": "cron", "cron": None},
            "timezone": "UTC",
            "timeout": 60,
            "enabled": True,
        },
    )
    assert bad_cron.status_code == 400
    assert bad_cron.json()["detail"]["code"] == "invalid"

    wrong_body = client.post("/api/scripts", json=[])
    assert wrong_body.status_code == 422
    assert wrong_body.json()["detail"]["code"] == "validation"
    assert isinstance(wrong_body.json()["detail"]["message"], str)


def test_successful_login_does_not_reset_failed_attempts_against_another_account(tmp_path):
    client = authenticated_client(tmp_path)
    response = client.post(
        "/api/admin/users",
        json={"username": "operator", "password": "operator pass 123", "role": "operator"},
    )
    assert response.status_code == 200, response.text
    assert client.post("/api/logout").status_code == 200

    for _ in range(9):
        assert client.post(
            "/api/login", json={"username": "owner", "password": "wrong password"}
        ).status_code == 401

    assert client.post(
        "/api/login", json={"username": "operator", "password": "operator pass 123"}
    ).status_code == 200
    assert client.post(
        "/api/login", json={"username": "owner", "password": "wrong password"}
    ).status_code == 401
    assert client.post(
        "/api/login", json={"username": "owner", "password": "wrong password"}
    ).status_code == 429


def test_manifest_rejects_parameter_and_variable_names_that_tasks_cannot_supply(tmp_path):
    client = authenticated_client(tmp_path)
    manifests = [
        {"parameters": [{"key": "TASK_RUN_ID", "required": True}]},
        {"required_variables": ["PATH"]},
    ]

    for index, manifest in enumerate(manifests):
        response = client.post(
            "/api/scripts",
            json={"name": f"Invalid manifest {index}", "source": "print('never')", "manifest": manifest},
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "invalid"


def test_html_and_static_assets_are_revalidated_after_upgrade(tmp_path):
    client = TestClient(create_app(state_dir=tmp_path, database_url=f"sqlite:///{tmp_path / 'cache.db'}"))

    assert client.get("/").headers["cache-control"] == "no-store"
    assert client.get("/static/app.js").headers["cache-control"] == "no-store"
