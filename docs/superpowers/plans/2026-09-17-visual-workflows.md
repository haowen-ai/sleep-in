# Sleep In Workflow Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development to implement and review each owned subsystem. Preserve user state and separate delivered evidence from design targets.

**Goal:** Implement the approved visual, multilingual workflow product with true data dependencies, form schedules, and a local always-on Mac launch path.

**Architecture:** Retain FastAPI, transactional Store and authentication. Add workflow-specific domain, execution and API modules, a canvas editor, and a native local supervisor. n8n executes published graphs through authenticated per-node calls; independent worker processes run trusted code. Preserve v1 records and routes for migration.

**Tech Stack:** Python 3.12/FastAPI/SQLAlchemy, vanilla browser modules and SVG, Node/n8n, SQLite local and PostgreSQL optional, macOS process supervisor and idle-sleep assertion.

**Spec:** docs/PRD.md and docs/PRD.zh-CN.md

## Global Constraints

- English initial UI; full Simplified Chinese switching preserves drafts.
- Only language scripts and SQL receivers in the business-node catalog.
- Visual upstream field selection, typed JSON and file contracts, immutable published versions.
- Form schedules; no Cron editor on the new workflow path.
- Trusted local runtime, default local account displayed only until changed; no reset of existing users.
- On AC and battery: default background service; browser closure and zero tasks do not stop it.
- Do not change this user's login items or power settings as incidental verification.
- No public claim of signed/notarized distribution, Oracle compatibility, battery longevity or 24-hour uptime without actual evidence.

## Shared interface

Workflow object: id, name, description, nodes, edges, params, schedule, timezone, enabled, timeout, published_version_id.
Node: id, name, kind (python/javascript/shell/sql/java/c/cpp), source, config, inputs, position {x,y}.
SQL config: dialect (sqlite/postgresql/mysql/oracle), connection_id.
Inputs: target field to {source:constant,value:...}, {source:parameter,path:...}, or {source:node,node_id:...,path:...}.
Edges: source and target node IDs. Optional edge conditions and join policies must be specified in the backend API contract before frontend consumption.
Routes: /api/workflows GET/POST; /api/workflows/{id} GET/PUT; /publish, /run, /preview POST; /api/workflow-runs GET; /api/workflow-runs/{id} GET; /cancel POST; /api/workflow-templates GET; /api/connections GET/POST; /api/runtimes GET.
Backend publishes exact contract in docs/WORKFLOW-API.md before frontend integration.
App route mounting is owned by integration, via register_workflow_routes(app, store, require).

## Test-first gate (user steering)

- [x] Pause feature work and design explicit data/schedule/UI/lifecycle/security cases first.
- [x] Fix edge-vs-input, no-input, optional defaults, null semantics and cross-language fixtures in docs/testing/contract-decisions.md.
- [x] Complete cross-document review and validate case IDs/links before resuming implementation.
- [x] Translate P0 golden paths into failing tests before further feature changes; maintain case-to-test execution evidence.

Canonical test plan: docs/TEST-PLAN.md and docs/TEST-PLAN.zh-CN.md. Detailed case inventory is docs/testing/case-index.json. User's latest instruction prioritizes this reviewable test artifact before further development.

## Task 1: Workflow backend and runtime

Files: taskconsole/workflows*.py, taskconsole/schedule.py, tests/test_workflows*.py, docs/WORKFLOW-API.md.
- [x] Write failing behavior tests: SQL rows -> Python summary -> JS output; cycle/mapping rejection; publish snapshots; duplicate admission and cancellation.
- [x] Implement validation, publications, form schedule preview, authenticated CRUD/run/connection/runtime APIs.
- [x] Implement per-language file protocol and compilation/cache, safe SQL parameter binding, driver readiness.
- [x] Compile real n8n graph and authenticated execution adapter; persist node/run logs and artifacts; handle failure and stale leases.
- [x] Verify real runtime roundtrip and targeted tests. Review against PRD before integration.

## Task 2: Visual workflow interface

Files: taskconsole/static/workflows.js, workflow-model.js, workflow-i18n.js, workflow.css; app.js/i18n.js minimal integration; tests/test_workflow_frontend.py.
- [x] Test graph editing/mapping state and bilingual schema before implementing graph model.
- [x] Add workflow list/templates/editor with actual drag/drop/move/port connections, selection, undo and validation.
- [x] Add input field picker, code configuration, connection/runtime management, publication/test/run inspection and no-Cron schedule forms.
- [x] Integrate default-login card using bootstrap.local_account when supplied; keep v1 routes available.
- [x] Verify syntax/model tests; integration owns live browser checks.

## Task 3: Native local launch and persistent service

Files: taskconsole/local*.py, launch-mac.command, packaging scripts, tests/test_local*.py, docs/MAC.md.
- [x] Test local fresh bootstrap, existing-user preservation, changed-password hiding and service lifecycle policy.
- [x] Implement local supervisor with single-instance lock, app/n8n/worker startup, AC/battery idle-sleep protection and stop/status commands.
- [x] Implement double-click launcher with dependency readiness/errors; package app if available tooling allows; never claim signing without certificate.
- [x] Offer explicit startup registration with user consent; do not silently modify host login items.
- [x] Verify stopped/restart behavior and document exact implemented vs unavailable packaging capabilities.

## Task 4: Integration, actual execution and review

Files: taskconsole/app.py, deployment configuration, docs/VERIFICATION.md, README files, tests/test_workflow_integration.py.
- [x] Mount APIs/local bootstrap and launch isolated local instance without touching existing state.
- [x] Test immutable graph through actual n8n with SQL -> Python -> JS outputs and one scheduled occurrence.
- [x] Inspect desktop English/Chinese UI, drag connections, saved workflows and run output via browser.
- [x] Review security/lifecycle/duplicate and cancellation boundaries; fix findings and rerun covering tests.
- [x] Run full regression suite, package checks and document evidence/remaining external release gates.
- [ ] Merge reviewed work into repository, exact-path stage and push, verify remote hash; provide local test URL.

## Scope at the preview checkpoint

These checked implementation tasks establish the core development preview, not the entire PRD or every designed acceptance case. Immutable runtime packs, single-node testing, artifact inputs, notifications and further release gates are listed explicitly in docs/testing/RESULTS.md. Case inventory and execution counts are kept separate.
