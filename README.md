# Sleep In · 不再早起

**Let your tasks take the early shift.**

A visual, drag-and-drop low-code platform for complex scheduled workflows — now being built.

English · [简体中文](README.zh-CN.md)

[![CI](https://github.com/haowenchen0811/sleep-in/actions/workflows/ci.yml/badge.svg)](https://github.com/haowenchen0811/sleep-in/actions/workflows/ci.yml) · [MIT license](LICENSE)

Sleep In is being redesigned around a visual workflow canvas: connect SQL receivers and Python, JavaScript, Shell or other language scripts, map outputs to inputs, and schedule the entire flow with ordinary forms. n8n will orchestrate the published graph behind an independently designed interface.

> **Current status:** the repository still implements the v1 Python task console. The drag-and-drop workflow platform and always-on Mac installer below are v2 requirements, not released features. See the [PRD](docs/PRD.md), [中文 PRD](docs/PRD.zh-CN.md) and [verification status](docs/VERIFICATION.md). Run only trusted scripts; the current worker is not a hostile-code sandbox.

## Why Sleep In?

Monday's meeting should not mean an early alarm just to pull data. Sunday night should still be yours. Prepare the workflow once, let it collect and process the data on schedule, and wake up to the results.

For one isolated task, a script timer or an AI-scheduled action may already be enough. The problem gets harder when two databases must be queried, their outputs combined, a Python script computes metrics, and a JavaScript step prepares the final report. Those steps have dependencies, shared inputs, different environments and failures to inspect. **That reusable, visible workflow is why Sleep In exists.** AI can help write a script, but building and running a workflow must not require an AI service.

## The workflow we are building

Drag language nodes onto the canvas, connect their ports, select upstream fields, test each step, then publish and choose a human-readable schedule. Templates are editable starting points; the canvas is the main authoring surface. Branches, parallel paths, joins and node-level run history make complex jobs inspectable.

```mermaid
flowchart LR
  T[Every Monday at 7 AM] --> P[SQL · PostgreSQL]
  T --> O[SQL · Oracle]
  P -->|rows| PY[Python · combine and calculate]
  O -->|rows| PY
  PY -->|summary| JS[JavaScript · prepare report]
  JS --> R[Downloadable results]
```

This is a planned example; it does not claim these integrations already work in v1. Node categories stay focused on languages and SQL dialects. Reports and calculations are script logic, not filler catalog nodes. Schedules use forms rather than Cron. English comes first, with a Simplified Chinese switch.

## Install once. Keep workflows running.

The Mac edition is designed to start its background service automatically after setup and required macOS approval, and keep running **on both battery and AC power**. Closing the browser or application window, unplugging, or finishing today's job should not stop tomorrow's schedule. No rented server, separate n8n account or nightly activation is required. Initial local credentials will be visible on the login page and changeable in Account settings.

The service will request idle-sleep prevention while allowing screen lock and display sleep. Battery power is supported, but keeping a Mac awake consumes energy; software cannot run after power is exhausted or the machine is shut down, and idle-sleep prevention does not override lid-close or explicit system sleep. [Apple documents these limits](https://developer.apple.com/documentation/iokit/kiopmassertiontypepreventuseridlesystemsleep). The installer and battery-runtime behavior still need implementation and verification.

**One-click setup. One-click run. More sleep.**

## Current v1: from script to schedule

```mermaid
flowchart LR
  A[Choose a script] --> B[Set a schedule]
  B --> C[Preview and enable]
  C --> D[Check logs and results]
  style A fill:#edf7ef,stroke:#9dc9ab,color:#234d32
  style B fill:#edf7ef,stroke:#9dc9ab,color:#234d32
  style C fill:#edf7ef,stroke:#9dc9ab,color:#234d32
  style D fill:#edf7ef,stroke:#9dc9ab,color:#234d32
```

## Current v1 capabilities

- Start with three standard-library examples, or publish a Python file or ZIP project.
- Choose manual, interval, daily, weekdays, weekly, monthly, or five-field cron schedules.
- Preview upcoming runs in an explicit IANA timezone, including daylight-saving rules.
- Pin a script version to each task; queued runs keep their original parameters.
- Read stdout and stderr, cancel a run, set a timeout, and download output files.
- Keep secrets in write-only variables, with administrator and operator roles.
- Use English by default. Switch to 简体中文 at any time, including before sign-in.

No company-specific configuration, cloud account, SMTP account, or existing n8n account is required for the examples.

## Run the current development version

Install [Docker Desktop](https://www.docker.com/products/docker-desktop/) or Docker Engine with the Compose plugin. Keep Docker running whenever you want scheduled tasks to execute.

```bash
git clone https://github.com/haowenchen0811/sleep-in.git
cd sleep-in
docker compose up -d --build
```

The initial development checkout builds the application image locally. PostgreSQL and n8n use pinned upstream images. Prebuilt application images are produced by the release workflow after tagged releases; do not assume an unpublished image exists.

Get your local, single-use setup token:

```bash
docker compose exec web python -m taskconsole setup-token
```

Open [http://localhost:8080](http://localhost:8080), enter the token, and choose an administrator username, a password of at least 12 characters, and your timezone. The initial interface is always English; select **简体中文** if you prefer it.

1. Select **Create task** and the **Create an output file** example.
2. Choose **Interval**, set **Every (minutes)** to **1**, check the preview, then create and enable it.
3. Open the execution when it finishes and download `hello.txt`.

n8n initializes its dispatch workflow automatically. Its editor and database are not exposed to the host. A task is only reported as ready after a recent n8n heartbeat. Saving a schedule does not immediately execute the script.

## Write a script

For a single Python file, define a synchronous `main(params)` function:

```python
def main(params):
    print(f"Hello, {params.get('name', 'world')}!")
```

Or use a normal top-level script. Read structured parameters and create outputs through the portable environment contract:

```python
import json
import os
from pathlib import Path

params = json.loads(Path(os.environ["TASK_PARAMS_FILE"]).read_text())
output = Path(os.environ["TASK_OUTPUT_DIR"])
(output / "result.txt").write_text(params.get("message", "Hello!"))
```

ZIP projects have a `main.py` entrypoint and may include a `requirements.txt` with pinned `package==version` dependencies. Dependencies are prepared at publication, not at each run. See [script authoring](docs/SCRIPTS.md) for entrypoint semantics, limits, secrets, and dependency behavior.

## Current v1 architecture

```mermaid
flowchart TB
  Browser[Web console] --> API[FastAPI]
  API --> DB[(PostgreSQL)]
  n8n[n8n: every 5 seconds] -->|authenticated dispatch heartbeat| API
  Worker[Python worker] --> DB
  Worker --> Scripts[Versioned Python scripts]
  Scripts --> Results[Logs and output files]
  API --> Results
  classDef service fill:#edf7ef,stroke:#9dc9ab,color:#234d32
  class Browser,API,DB,n8n,Worker,Scripts,Results service
```

The application owns task definitions and timezone calculations. A single, automatically provisioned n8n workflow wakes dispatch; it does not contain users' source code or parameters. PostgreSQL transactions serialize due-job creation and claims. The worker runs trusted Python in a separate process group, with a minimal environment and no Docker socket.

[Architecture](docs/ARCHITECTURE.md) · [Operations and backup](docs/OPERATIONS.md) · [Contributing](CONTRIBUTING.md)

## Everyday operations

```bash
# Service status
docker compose ps

# Application logs
docker compose logs --tail=100 web worker n8n

# Stop while preserving your data
docker compose stop

# Start again
docker compose start
```

Data is stored in named volumes. **Do not run `docker compose down -v` unless you intend to erase those volumes.** Back up before upgrades; follow the restore procedure rather than copying a live database directory.

## Deliberate boundaries

- One trusted workspace, one worker service, up to 16 concurrent executions. This is not multi-tenant SaaS.
- Missed schedules are recorded as skipped instead of replayed. Weekdays mean Monday–Friday, without holiday calendars.
- Overlapping runs of the same task are skipped. External side effects cannot be guaranteed exactly once; scripts should use `TASK_RUN_ID` when implementing idempotency.
- Your machine must stay on. Sleeping a laptop or stopping Docker stops scheduling.
- A recipient field passes addresses to your script; it does not send email by itself.
- Advanced shared-runtime editing and built-in email notifications are later work.

## Development

Python 3.12+ is required for local development. Docker Compose is the supported full-system deployment path.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
python -m pytest -q
```

For an isolated API/UI development instance, SQLite is supported by the test repository adapter:

```bash
python -m taskconsole init
python -m taskconsole serve
# In another terminal, with the same virtual environment:
python -m taskconsole worker
```

This local mode has **no automatic n8n heartbeat**. Manual runs work; scheduled readiness correctly remains unavailable. Use Compose for the complete n8n integration.

## License

The original code in this repository is [MIT licensed](LICENSE). **n8n is a separately licensed dependency** and is not relicensed by this project. See [third-party notices](THIRD_PARTY_NOTICES.md). This project is independent and is not affiliated with n8n.
