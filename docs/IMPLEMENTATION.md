# n8n Task Console implementation plan

Goal: an independently written, English-first Python task console with an explicit Simplified Chinese switch, based on the approved PRD.

Architecture: same-origin FastAPI + browser JavaScript; PostgreSQL transactional state; a separate Python worker; n8n invokes an authenticated dispatch heartbeat. The application owns timezone mathematics and due dates so preview and triggering agree. A single n8n workflow avoids per-task provisioning and API-key creation. No external n8n editor is exposed. This is a documented refinement of the PRD's derived-workflow synchronization model.

Spec: the user-approved PRD v0.2, with the latest user instruction overriding browser-language detection: English always on first visit, Chinese only after explicit selection. The source PRD and private screenshots remain outside the repository.

## Global constraints

- Independent code and synthetic samples only. No old deployment addresses, corporate names, secrets, business data, or resignation copy.
- Python scripts and ZIP projects; trusted administrators only; no Docker socket; not a hostile-code sandbox.
- Seven schedule kinds: manual, interval, daily, weekdays, weekly, monthly, cron. IANA timezones, skip DST gaps, first fold only. No backlog replay.
- Immutable versions and run snapshots. Global concurrency 2 by default, per-task overlap prevention, idempotent manual submission, timeout, process-group cancellation.
- Initial default locale en; zh-CN is user selected. Account persistence, unsaved form preservation, no translation of user data.
- No fixed admin credentials. First-use local setup token, hashed passwords, revoked sessions, CSRF, server authorization, last-admin protection.
- Source-only MIT licensing, n8n independently licensed. No claim of release-readiness until actual checks pass.

## Component contracts

### Runtime module (taskconsole/runtime.py)
`inspect_source(source: str) -> dict` yields {mode: 'function'|'script'} using AST only. `prepare_bundle(data: bytes, filename: str, dest: Path) -> dict` validates upload and writes accepted files, returns mode and requirements. `build_environment(source_dir: Path, runtime_dir: Path) -> dict` returns {python, freeze, log}. `execute(source_dir: Path, mode: str, params: dict, output_dir: Path, python: str, timeout: int, cancel: Callable[[], bool], log: Callable[[str,str],None], env: dict) -> dict` runs code once and returns {status, exit_code, reason, artifacts:[{name,size}]}. Logs delivered decoded UTF-8. Never inherits control-plane credentials. Emits TASK_PARAMS_FILE, TASK_OUTPUT_DIR, TASK_RUN_ID supplied by caller. 20 MiB log cap, 100 MiB /100 file artifact limits. Process group termination and no symlink artifacts.

### Schedule module (taskconsole/schedule.py)
Root owns `validate_schedule(spec:dict, timezone:str)` and `next_runs(spec, timezone, after:aware datetime, anchor:aware datetime|None, count=5) -> list[datetime]`. spec keys: kind, every (minutes), time ('HH:MM'), weekdays (0 Monday..6 Sunday), day (1..31), cron. Returns UTC datetimes.

### HTTP API (taskconsole/app.py), owned by root
All responses JSON except static files/downloads. Errors `{detail:{code,message}}`. GET /api/bootstrap -> {initialized,user|null,csrf|null,timezone,scheduler:{status,last_tick},version}. User {id,username,role,locale,enabled}. POST /api/setup {token,username,password,timezone,locale}; POST /api/login {username,password}; returns {user,csrf}; session cookie HttpOnly SameSite strict. Every authenticated mutation requires X-CSRF-Token. POST /api/logout; PATCH /api/me {locale}; POST /api/me/password {current_password,password}.

GET /api/scripts -> list with {id,name,description,archived,default_version,versions:[{id,number,status,mode,created_at,manifest,build_log,freeze}]}. Operator sees published metadata only, no source. POST /api/scripts {name,description,source,manifest,requirements}; alternatively multipart /api/scripts/upload fields name,description,manifest JSON,file. PATCH /api/scripts/{id} updates draft name,description,source,manifest,requirements. GET /api/scripts/{id} includes draft source for admin. POST /api/scripts/{id}/check; POST /api/scripts/{id}/publish -> version (may be synchronous dependency build, UI loading state); POST /api/scripts/{id}/archive {archived}; POST /api/scripts/{id}/default {version_id}; POST /api/scripts/{id}/retire {version_id}.

GET /api/tasks -> list {id,name,version_id,script_name,params,recipients,schedule,timezone,timeout,enabled,archived,revision,next_run,last_run,sync_status}. POST /api/tasks and PUT /api/tasks/{id} accept {name,version_id,params,recipients,schedule,timezone,timeout,enabled}. GET /api/tasks/{id}; POST /api/tasks/{id}/toggle {enabled}; POST /api/tasks/{id}/archive {archived}; POST /api/tasks/{id}/run header Idempotency-Key -> execution. POST /api/preview {schedule,timezone} -> {times:[ISO UTC strings]}.

GET /api/executions?task_id=&status=&trigger=&start=&end=&page=1 -> {items,total,page}. Run {id,task_id,task_name,status,trigger,created_at,started_at,finished_at,reason,exit_code,params,version_id,script_name,timezone,artifacts:[{name,size}],logs_expired,logs_truncated}. GET /api/executions/{id}; GET /api/executions/{id}/logs?stdout_offset=0&stderr_offset=0 -> {stdout,stderr,stdout_offset,stderr_offset,truncated,expired}; offsets are Unicode character counts. POST /api/executions/{id}/cancel. GET /api/executions/{id}/artifacts/{name:path} authenticated download.

GET /api/admin/settings -> {timezone,concurrency,log_retention_days,metadata_retention_days,scheduler,worker}; PATCH same mutable fields. GET /api/admin/users -> list users; POST {username,password,role}; PATCH /{id} {enabled,role,password optional}. GET /api/admin/variables -> [{id,name,scope,updated_at}] no values. POST {name,scope,value}, DELETE /{id}. scope 'instance' or script ID. GET /api/admin/audit -> list. All admin routes enforce admin.

POST /internal/tick header X-Dispatch-Token shared local secret. Body optional; server derives time. Transactional due-task dispatch, records last tick. GET /healthz liveness; GET /readyz database readiness.

### Deployment (deploy/, Dockerfile, compose.yaml)
APP_STATE_DIR=/state, DATABASE_URL=postgresql+psycopg://..., shared restricted state mount web/worker; secret file /state/dispatch-token for n8n initialization (never printed). CLI `python -m taskconsole init`, `worker`, `serve`, `setup-token`, `reset-password`, `backup`, `restore`. Root implements CLI. init is idempotent creates schema, local secrets, three samples. n8n provisioning uses supported CLI against a pinned version; tick HTTP URL http://web:8080/internal/tick. Only web binds 127.0.0.1:8080. Images can build locally; multiarch release CI after successful tests. Environment's missing Docker must be recorded; CI is separate evidence from local unit checks.

## Execution tasks and checks

- [ ] 1. Runtime: failing tests for AST no side effects, one execution, unsafe archives, Unicode logs, outputs, timeout and cancellation; implement runtime and sample scripts; run tests.
- [ ] 2. Core: failing tests for monthly/DST schedules, setup/login/revocation/RBAC, snapshots, overlap/dedupe, cancellation, variable non-disclosure; implement persistence/API/worker/CLI; run unit and API integration tests.
- [ ] 3. Frontend: implement all P0 routes against this contract; English/Chinese dictionaries with identical keys; test locale fallback/persistence and unsaved fields; browser verify synthetic first-run, task and log flows.
- [ ] 4. Deployment: verify pinned n8n CLI provisioning and heartbeat; Docker Compose, healthchecks, CI PostgreSQL and compose smoke, release workflow; repeat boot does not duplicate heartbeat.
- [ ] 5. Delivery: English README first, linked Chinese README, architecture/script contracts, backup/security/license docs. Review, scan staged exact paths, create GitHub repo, push over verified SSH, verify remote SHA and CI.

Tests use real temporary directories, database transactions and subprocesses, never real business jobs. Each implementer writes and runs the behavioral tests before implementation. Public test results state which environments actually ran; unavailable OS runs remain unverified.
