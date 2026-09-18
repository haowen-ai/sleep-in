# Complete workflow release implementation plan

> **For agentic workers:** Use superpowers:subagent-driven-development and test-driven-development. User authorizes completing the existing PRD and correcting free-direction canvas interaction. Do not stop at a preview checkpoint.

**Goal:** Complete implementable initial-release PRD behavior, exercise actual runtime paths, and report external release gates precisely.
**Architecture:** Retain the independent workflow contract compiled to n8n. Add focused modules for runtime packs, operational policies and portable artifacts. Keep native local and Compose profiles on the same workflow engine. Frontend remains English-first and bilingual.
**Tech Stack:** Python/FastAPI/SQLAlchemy, browser JS/SVG, n8n, native macOS companion, SQLite/PostgreSQL.
**Spec:** `docs/PRD.md`, `docs/PRD.zh-CN.md`, plus the interaction amendment below.

## Global constraints and interaction amendment

- This work supersedes the PRD's left-to-right-only layout. Layout is a presentation choice; dependency direction is semantic and independent of x/y coordinates.
- Choose top/right/bottom/left connector sides from node geometry, retain unmistakable source-to-target arrowheads, route outside intervening cards with rounded orthogonal paths. Support all quadrants, vertically aligned nodes and reverse horizontal ordering. Draw temporary connections and enlarge hit areas; reject cycles with feedback.
- Free pan, cursor-anchored zoom, fit, automatic horizontal/vertical layouts, keyboard movement, outline selection and undo must preserve logical mappings. Labels must not cover ports or card bodies; overlapping cards may be moved freely, but no false claim of a valid unobstructed route when endpoints overlap.
- Tests precede implementation. Include SQL-to-Python, no upstream data, null/empty/missing distinctions, artifacts, frozen runtimes, node-only test, retries, notifications, retention, backup/restore, upgrade rollback and actual n8n graphs.
- Only synthetic/isolated test data. Preserve current localhost:8766 state and unsaved browser drafts. No credentials in Git or Wiki. No physical lock/reboot/unplug, host permission changes or external notifications as incidental tests.
- Developer ID/notarization and real hardware/power/third-party database availability are evidence gates, never fabricated or silently removed from acceptance.

## Task 1: Canvas and complete authoring interaction

**Files:** static/workflows.js, workflow-model.js, workflow-i18n.js, workflow.css, new workflow-geometry.js; tests/test_workflow_frontend.py and new geometry tests.
**Interfaces:** Preserve current workflow JSON/API. Integrate added runtime, node-test and operational API contracts after their owners document them. Own all frontend edits.
- [ ] Add failing geometry cases: left/right/up/down/diagonal; intervening card; same-position endpoints; after node move and zoom. Assert path segments do not enter unrelated node rectangles and arrow direction ends at target.
- [ ] Implement adaptive ports, obstacle-aware routing, visible arrowheads, panning and cursor zoom; eliminate fixed 3000px clipping and one-way-only interaction.
- [ ] Add keyboard and panel focus controls, responsive inspector, layout choice, selected-edge editing, coherent node/input affordances.
- [ ] Complete UI for runtime packs/projects, node test with explicit sample input, artifacts/context/credentials, notifications/policies and migrations/backup as API contracts arrive. Keep advanced controls collapsed and synthetic-template journey short.
- [ ] Run model and actual browser tests at multiple sizes; review screenshot reproduction before accepting.

## Task 2: Execution contract completion

**Files:** workflows.py, workflows_runtime.py (coordinate runtime owner), workflows_n8n.py, workflows_transfer.py, new workflow execution modules/tests.
**Interfaces:** Service owns workflow/run/node records. Runtime owner exposes immutable profile resolution hooks. Operations owner exposes completion/periodic hooks in workflows_operations.py; coordinate signatures before integration.
- [ ] Test and implement node-only test admission with explicit sample or authorized historical inputs, no upstream execution, immutable draft snapshot and test marker.
- [ ] Test and implement authorized artifact inputs materialized in downstream working directory with checksum/quota/traversal checks; complete oversized outputs as files, not truncation.
- [ ] Test and implement bounded explicit node retries with separate attempts, deadlines/cancel safety, no automatic replay of unknown write effects; retain n8n as orchestrator.
- [ ] Implement async submit/status node adapter rather than long open HTTP request; duplicate callbacks and recovery preserve one execution.
- [ ] Add trigger API authorization/schema/idempotency/version selection, bounded overlap queue and missed-occurrence latest-once policy; preserve disabled triggers.
- [ ] Verify real n8n chains/branches/retries/cancellation/artifacts and record exact evidence.

## Task 3: Immutable runtimes and source projects

**Files:** new workflows_packs.py, source/project helpers, runtime tests. Coordinate narrow integration points in workflows_runtime.py with Task 2. Do not edit frontend.
**Interfaces:** Runtime version IDs stored in node config; resolve to ready immutable pack, build sources at publication; APIs under /api/runtime-profiles and /api/runtime-profiles/{id}/build. Document exact JSON contract early in docs/RUNTIME-API.md.
- [ ] Test then implement Draft/Building/Ready/Failed profiles, versioned copy-on-change, language/platform identity, isolated paths, build logs and referenced-workflow reporting.
- [ ] Lock Python dependencies and JS npm dependencies with real hashes/lockfiles, shared modules, Python/JS entrypoints, ESM/CJS; install at build time, never implicitly at run time.
- [ ] Implement validated file/ZIP projects, entrypoint checks, digest caches; Java source/JAR and Maven/Gradle builds, C/C++ multiple sources, admin custom argv protocols. Enforce no archive traversal/symlink escape.
- [ ] Add user-space optional toolchain installation manifests with integrity checks, progress/readiness and genuine self-tests. Never silently modify host tools.
- [ ] Actual Python/JS/Shell/C/C++/Java protocol tests and all 49 ordered language pairs with fixed values; obtain optional runtimes through reputable official sources when feasible.

## Task 4: Operational policies and local/server delivery

**Files:** workflows_operations.py, operations tests, app.py mounting, deploy/Compose, packaging/local modules, bilingual PRD/docs.
**Interfaces:** register_operations_routes(app,store,require); WorkflowOperations(store).on_run_terminal(run_id) and .tick() are idempotent hooks used by Task 2. No external sends in tests: use local SMTP/webhook fixtures.
- [ ] Test then implement email/webhook channel settings, masked terminal-event outbox, presets and explicit local test targets; delivery failures independent from business runs and tests off by default.
- [ ] Implement retention preview/cleanup with active-reference protection; backup/restore validation, schema migrations, v1 conversion to disabled workflow and safe old-dispatch handoff.
- [ ] Implement validated local update backup/switch/rollback and user-visible recovery; improve menu health/power state and permission paths without mutating this host's settings.
- [ ] Update Compose to run the same v2 engine and real graph smoke, not a v1-only deployment.
- [ ] Complete novice template-to-time-to-readiness path, run naming, defaults and all missing PRD settings with frontend owner.
- [ ] Run real local external-database fixtures where feasible; build, package, integration, browser and full regression tests. Mark each A01–A23 as implemented and evidence-backed or an exact external blocker; do not replace an unmet release gate with a completion claim.

## Task 5: Integrated review and delivery

- [ ] Audit every PRD initial-release requirement and all test suites; correct gaps, tests and documentation.
- [ ] Independent review of lifecycle/security/data integrity and actual screenshots, resolve findings with regression tests.
- [ ] Run the complete suite, actual n8n/Compose checks, packaged self-test and clean-state setup. Verify UI all directions and no implicit input transfer.
- [ ] Exact-path Git staging, inspect cached paths, commit/push authorized repository and verify hashes. Update localhost without losing user state or unsaved drafts; document actual remaining external gates.

## Integrated implementation checkpoint

Tasks 1–4 implemented and independently reviewed. Current full local suite: 443 passed / 26 fixture skips; frontend completion: 32 passed. Task 5 CI/clean Compose/external SQL and final delivery remain in progress. Hardware/distribution acceptance remains explicitly open in docs/testing/COMPLETE-RESULTS.md; it is not replaced by mocked tests.
