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


def request(path, body=None, csrf=None, method=None):
    headers = {"Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if csrf:
        headers["X-CSRF-Token"] = csrf
    with client.open(urllib.request.Request(base + path, data=data, headers=headers,method=method), timeout=120) as response:
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

# The workflow profile must execute an actual n8n graph, not just a v1 heartbeat.
from datetime import datetime, timedelta, timezone
template=request('/api/workflow-templates')[0]
workflow=request('/api/workflows',{**template,'name':'Compose real workflow'},csrf)
request('/api/workflows/'+workflow['id']+'/publish',{},csrf)
workflow=request('/api/workflows/'+workflow['id'])
workflow.update(enabled=True,timezone='UTC',schedule={'kind':'interval','every':1,'unit':'minutes','anchor':(datetime.now(timezone.utc)+timedelta(seconds=8)).isoformat()})
request('/api/workflows/'+workflow['id'],workflow,csrf,'PUT')
deadline=time.time()+120
while time.time()<deadline:
    runs=request('/api/workflow-runs?workflow_id='+workflow['id'])
    successful=[r for r in runs if r['status']=='succeeded']
    if successful:
        actual=request('/api/workflow-runs/'+successful[0]['id'])
        if actual.get('adapter_finished_at') and actual.get('n8n_execution_id'):break
    if any(r['status'] in {'failed','timed_out'} for r in runs):raise SystemExit('Actual workflow failed: '+json.dumps(runs))
    time.sleep(2)
else:raise SystemExit('Real n8n scheduled graph did not finish')
assert actual['engine']=='n8n' and actual['version_id'] and actual['n8n_execution_id']
assert actual['nodes']['summary']['output']['data']['summary']=={'count':3,'total':'30.75'}
assert all(n['status']=='succeeded' and len(n['attempts'])==1 for n in actual['nodes'].values())
artifact=next(a for a in actual['artifacts'] if a['name']=='report.txt')
with client.open(base+'/api/workflow-runs/'+actual['id']+'/artifacts/'+artifact['id'],timeout=10) as response:
    assert response.read().decode()=='3 orders • 30.75\n'
print('actual n8n scheduled SQL -> Python -> JavaScript graph and exact artifact observed')
PY

before="$(docker compose exec -T postgres psql -U taskconsole -d taskconsole -Atc "select count(*) from n8n.workflow_entity where id='task-console-heartbeat'")"
test "$before" = "1"
docker compose stop n8n
docker compose run --rm n8n-init
docker compose start n8n
after="$(docker compose exec -T postgres psql -U taskconsole -d taskconsole -Atc "select count(*) from n8n.workflow_entity where id='task-console-heartbeat'")"
test "$after" = "1"
echo "one heartbeat workflow remains after repeated bootstrap"
