# Workflow backend execution evidence

Scope: current native development checkout, synthetic isolated state, and the tests listed below. This is **partial implementation evidence**, not completion of the entire PRD or every designed matrix cell. No live user workflow state, login item, system power setting or external production database was changed by these tests.

## Environment actually used

- macOS arm64 native processes.
- Python 3.12.14; Node.js 24.19.0; n8n 2.39.7 native CLI.
- SQLite 3.53.1; Bash 3.2.57; Apple Clang 21.0.0 for C and C++.
- Java: **BLOCKED_ENV**. `/usr/bin/javac` is an OS launcher with no installed usable JDK; version probing fails. Java compilation/execution is implemented as an optional path but not verified on this machine.
- PostgreSQL/MySQL/Oracle: **BLOCKED_ENV for real-server claims**. Root's SQL adapter tests verify the named synthetic/translation behaviors only. No external database instance was exercised by this agent.

Runtime API currently reports executable/toolchain version probing and native compilation readiness. This must not be advertised as dependency-locked immutable runtime packs or a persisted runtime installation/self-test system. Native process isolation does not enforce container CPU/memory quotas.

## Commands and evidence levels

Core execution command (use local configured paths for Python/Node/n8n):

```sh
SLEEP_IN_NODE=/path/to/node \
SLEEP_IN_N8N_COMMAND='["/path/to/node","/path/to/n8n/bin/n8n"]' \
/path/to/python -m pytest \
  tests/test_workflows.py tests/test_workflows_n8n.py \
  tests/test_workflow_api_contract.py tests/test_workflow_schedule_contract.py \
  tests/test_workflow_join_contract.py tests/test_workflow_sql_contract.py -q
```

**Latest configured combined result: 104 passed, 1 skipped (Java unavailable), 2 dependency deprecation warnings, in 43.58 seconds.** The command included actual n8n execution; both real-n8n test functions passed, producing three real graphs (linear, branched, scheduled). Each test asserted a persisted actual n8n execution ID before temporary fixture cleanup. The two warnings concern Starlette/httpx/AnyIO deprecated test-client interfaces, not failing application assertions.

Without `SLEEP_IN_N8N_COMMAND`, the two real-n8n test functions are intentionally skipped. That skipped run does **not** establish graph execution. Configured executions use `import:workflow --input=...` followed by `execute --id=...`, with independent per-run n8n storage and loopback broker ports. A persisted authenticated finish callback and actual n8n execution ID are asserted; exit code zero alone is insufficient.

## Executed behaviors

| Design reference | Evidence | Result and limits |
|---|---|---|
| GOLD01–03 | Real SQLite → Python → JavaScript, complete F-ORDERS and Decimal total, exact artifact bytes; direct worker and actual n8n graph assertions | PASS for count3/total`30.75`, empty rows count0, one invocation per logical node, file-protocol output |
| GOLD05–06, MAP01–02/09–12/17 | Direct worker tests; actual n8n order-only consumer | PASS: no implicit upstream data; `{}` remains empty; typed named mappings; missing versus null/false/zero/empty; explicit defaults and parameter paths |
| MAP03–06, MAP19, DATA03, OUT02–03/13 | Validator and real helper/file workers | PASS for cycle/unreachable/type rejection, nullable unions, token paths, business schemaVersion fields wrapped as data, explicit envelope file mode |
| BR01–12, BR19, S04 | Root's 13 join truth-table tests plus actual n8n conditional branch → merge | PASS: normal skips do not fail; required failures block all/any; explicitly optional failure yields partial; any waits all markers; zero successful inputs block; native n8n merge does not cause duplicate worker invocation |
| BR05 | Direct worker append regression | PASS: mapped arrays concatenate in authored input-definition order into `inputs.items`; not a concurrency/relational join claim |
| SQL01–03, native SQLite contract | Actual synthetic database and root-owned SQL tests | PASS only for explicitly executed SQLite and adapter cases. External SQL dialect acceptance remains unverified |
| A07 / file protocol | Real Shell, C, C++ child-process roundtrip with Unicode/null/high-precision strings; real C/C++ compilation | PASS for these protocol fixtures on this Mac. Not all 49 ordered language pairs; Java BLOCKED_ENV |
| VER01–04 | Publication snapshots, queued-source immutability, stored interpreter selection, compiler-version cache invalidation | PASS for tested identities. Does not establish full dependency locking or immutable installed runtime images |
| CAN01–02, FAIL05–06 | Queued cancel, real long-node workflow deadline, adapter process-boundary cancellation injection | PASS for tested termination and terminal-state boundaries. SQL driver cancellation varies by actual dialect and remains a separate gate |
| RET04 / duplicate-admission and worker-lock subsets | API admission idempotency; one-attempt concurrent callback regression; worker single-instance lock | PASS for the covered key/marker/lock boundaries; the full 20-request/multi-worker stress variants have not all been executed. Duplicate active callbacks wait for terminal status instead of returning a running completion marker |
| N01–05 | Actual n8n CLI import/execute with authenticated individual worker nodes and merge barriers | PASS for linear and conditional diamond fixtures; this is not a Python DAG simulator |
| N13–14 | Recovery regression | PASS: authenticated terminal success survives an unfinished adapter lease; interrupted in-flight work is failed with uncertain-effects reason rather than replayed |
| Schedule intersection | Root's fixed schedule-oracle suite; real-clock anchor due after2s → actual n8n SQL/Python/JS | PASS for tested form calculator and real due admission; named trigger/timestamp dedupe, overlap skip records, pending-intent recovery after injected admission failure |
| API01–03 | Real FastAPI auth/CSRF/store tests owned by integration | PASS for covered access/authoring/publication/admission/preview/secret-metadata boundaries |

Test-first fixes were separately observed failing before implementation: missing workflow service; main-return envelope collision; token paths/nullable schema; nested transaction during draft admission; active overlap; interpreter drift; workflow deadline; node display identity; static type incompatibility; compiler cache identity; enabled trigger gating; append merge; worker single-instance lock; cancellation stuck in cancelling; terminal recovery overwrite; incorrect SLEEP_IN_RUN_ID; lost due occurrence after failed admission; duplicate running callback completion.

## Implemented surface

Authenticated workflow CRUD, incomplete draft errors, immutable publication/run snapshots, workflow draft tests, whole-workflow admission, session-authenticated API invocation, idempotency, default overlap rejection, parameter/data mapping, independent ordering edges, conditions, explicit joins, optional dependencies/defaults, native n8n execution, per-node logs/attempt records, run cancellation/deadlines, artifact collection/download/checksum verification, managed synthetic SQLite, connection metadata/create/test, native language toolchain probes/builds, and form schedules with multiple named scheduled triggers.

The scheduler commits the cursor and a durable occurrence intent together; the separate admission step retries via the same run idempotency key. Claims prevent simultaneous occurrence handling, and overlaps have visible records. Worker startup preserves authenticated terminal results and closes leftover adapter bookkeeping; uncertain in-flight effects are not automatically replayed.

## Remaining PRD work / do not advertise as delivered

- Standalone single-node tests/historical-sample selection API; current `/run` draft test executes a whole immutable draft.
- Locked dependency/project runtimes, versioned runtime pack installation/progress/self-tests, container workers, and signed clean-Mac toolchain installation. A compiler/executable probe is narrower evidence.
- The complete 49 ordered-pair matrix and external PostgreSQL/MySQL/Oracle server/driver/platform matrices; full production Oracle Thin claims are blocked.
- Credential/context expansion beyond documented safe references; write-only rotation/edit flows beyond any separately verified integration additions.
- Full dataset spill/reference materialization across workers, artifact-as-input mapping and paginated complete dataset previews. Current oversized inline SQL fails explicitly rather than truncating, and artifact download is implemented.
- Retry policy automation/backoff, checkpoint resume, bounded sequential admission queues and external-effect transactional guarantees. Attempts currently record one actual attempt; explicit full rerun remains possible.
- Notifications and recovery-notification state, retention cleanup, source ZIP/JAR project management, reusable script library and formal v1 migration remain separate work. Portable workflow template import/export was subsequently added by integration in `taskconsole/workflows_transfer.py`; see the separate transfer evidence below. This does not imply source-project packaging or automatic credential/runtime installation.
- Dedicated operator-only trigger-management routes, named API-trigger credentials/invocation controls. Current trigger authoring uses administrator workflow PUT; manual/API run admission uses the authenticated `/run` endpoint.
- Measured simultaneous n8n branch execution, 10/25/50-node scale, high-load/process-crash soak, physical locked-screen AC/battery tests, login/reboot recovery and signed distribution.

The native source-execution model is a trusted workspace. Time/output limits and process-group cancellation are implemented; hostile-code sandboxing and container-level resource isolation are not claimed.

Critical post-review fixes are included in that latest combined run: duplicate-callback terminal waiting, cancellation after n8n process termination, terminal-result preservation during lease recovery, correct execution environment identifiers, durable pending schedule admission after a failed admission call, and deterministic append merge.


## Subsequent generation-health and transfer integration

Worker heartbeat records now include the supervisor-provided `SLEEP_IN_INSTANCE_ID` as `instance_id` for ready, unavailable and stopped states. This allows local health checks to reject a previous supervisor generation. Two new regression variants were observed RED (missing field) before the change. They exercise the real worker loop and persisted Store while controlling only the command-probe and signal boundary; no n8n workflow or host service is started by this regression.

Portable template import/export is implemented separately in `taskconsole/workflows_transfer.py` and mounted by the main app. Its dedicated tests are [test_workflow_transfer.py](../../tests/test_workflow_transfer.py): source/graph preservation; removal of configured connection/runtime bindings, history and execution metadata; disabled/unpublished import with rebinding requirements; injection/version/shape/size rejection; and administrator/CSRF enforcement. Exports intentionally retain author-written source and literal values, with a review notice; they do not claim automatic redaction of a secret deliberately embedded in code or a literal. Refer to that test suite and the integration verification report for the separately tested surface instead of interpreting the earlier core-only limitations as missing template transfer support.

Post-integration targeted command: `python -m pytest tests/test_workflows_n8n.py tests/test_workflow_transfer.py -q` with no n8n command configured yielded **32 passed, 2 skipped, 2 dependency warnings in 2.59 seconds**. This includes both new generation-heartbeat variants and all 24 transfer tests. The two skips are actual-n8n tests whose earlier configured results are recorded above; this targeted run adds no new real-n8n claim.
