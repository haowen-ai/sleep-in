#!/bin/sh
set -eu

python3 - <<'PY'
import http.cookiejar
import json
import subprocess
import time
import urllib.request

base = "http://127.0.0.1:8080"
cookies = http.cookiejar.CookieJar()
client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cookies))


def request(path, body=None, csrf=None):
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    with client.open(urllib.request.Request(base + path, data=data, headers=headers), timeout=5) as response:
        return json.load(response)


token = subprocess.run(
    ["docker", "compose", "exec", "-T", "web", "cat", "/state/setup-token"],
    check=True,
    capture_output=True,
    text=True,
).stdout.strip()
session = request("/api/setup", {
    "token": token,
    "username": "compose-owner",
    "password": "ComposeSmoke-Only-2026",
    "timezone": "UTC",
    "locale": "en",
})
csrf = session["csrf"]

scripts = request("/api/scripts")
sample = next(script for script in scripts if script["id"] == "sample-3")
task = request("/api/tasks", {
    "name": "Compose scheduled output smoke",
    "version_id": sample["default_version"],
    "params": {},
    "recipients": [],
    "schedule": {"kind": "interval", "every": 1},
    "timezone": "UTC",
    "timeout": 30,
    "enabled": True,
}, csrf)

deadline = time.time() + 100
while True:
    bootstrap = request("/api/bootstrap")
    executions = request("/api/executions?task_id=" + task["id"])
    successful = [item for item in executions["items"] if item["status"] == "succeeded"]
    if bootstrap.get("scheduler", {}).get("last_tick") and successful:
        run = successful[0]
        break
    if time.time() >= deadline:
        raise SystemExit("scheduled sample did not complete after an n8n heartbeat")
    time.sleep(2)

if not any(item["name"] == "hello.txt" for item in run["artifacts"]):
    raise SystemExit("scheduled sample did not report hello.txt")
with client.open(base + f'/api/executions/{run["id"]}/artifacts/hello.txt', timeout=5) as response:
    output = response.read().decode()
if "Hello from your scheduled Python task!" not in output:
    raise SystemExit("scheduled sample output had unexpected contents")
print("heartbeat and scheduled sample output observed")
PY

before="$(docker compose exec -T postgres psql -U taskconsole -d taskconsole -Atc "select count(*) from n8n.workflow_entity where id='task-console-heartbeat'")"
test "$before" = "1"
docker compose stop n8n
docker compose run --rm n8n-init
docker compose start n8n
after="$(docker compose exec -T postgres psql -U taskconsole -d taskconsole -Atc "select count(*) from n8n.workflow_entity where id='task-console-heartbeat'")"
test "$after" = "1"
echo "one heartbeat workflow remains after repeated bootstrap"
