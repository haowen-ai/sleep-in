# Visual workflow completion and release gates

This ledger supersedes the earlier [core-preview results](RESULTS.md). Tests use synthetic, isolated state. The original test design was committed before implementation; later defects were reproduced with failing tests before fixes. Passing software tests do not establish physical power or public-distribution behavior.

## Round closeout — 2026-09-18

Production source `74f549aa3b9f2d2f127b2d5dc3cf64b677d0f8c3` is pushed; its [full CI](https://github.com/haowen-ai/sleep-in/actions/runs/35417044871) was still running at closeout. A new independent localhost preview at `http://127.0.0.1:8767/` has healthy app/worker and available n8n, with OS power assertions explicitly disabled for testing. All 65 packaged resources in the rebuilt local preview match this source; Swift build, ad-hoc signature and side-effect-free self-tests passed.

The final additional browser pause-one/global-pause scenario passed locally with actual supervisor/n8n (1 passed, 27.30 seconds). It changes test coverage only; its CI fixture configuration and exact case mapping accompany this closeout. No further feature expansion is included. CI results are pending, and formal signing, physical Mac trials and remaining acceptance gaps are not claimed complete.

## Previous candidate — 2026-09-18

Pushed candidate `863da8b43512ceb5195d68544e2da91a867b1024` has [same-revision CI evidence](https://github.com/haowen-ai/sleep-in/actions/runs/35415901572). Its default regression passed 766 tests with 299 fixture skips. The six actual integration groups passed: engine 116 / 3 skips; data 196; execution 60; recovery 68; schedule 93; scale/isolation 13. Compose passed. External SQL had 58 passes and one failure: after an Oracle transaction committed and cancellation completed at the node, the workflow remained `cancelling`. Actual Chromium passed 69 tests. This candidate is therefore not green. These job counts overlap and must not be summed as unique tests.

A local Mac preview passed Swift compilation, ad-hoc signature verification and side-effect-free self-tests; all 65 packaged resources match `863da8b`. It remains unnotarized and is not public-release approval.

### Follow-up verification candidate

The managed Apple-silicon Python installation exposed a real runtime-pack defect: copying its interpreter into a virtual environment without its shared library prevented `ensurepip` from starting. The pack now copies the required library before bootstrapping, includes it in the immutable manifest, and rejects subsequent tampering. Five actual managed-runtime/recovery cases passed locally, including independent missing Python, Node, n8n and worker components; 19 related pack tests passed with one optional npm-fixture skip. Python/npm repair transport in those cases restores genuine local bytes; it is not evidence of registry recovery. A new macOS CI job acquires a fresh managed installation through the real launcher before exercising the owned repair cases. That job has not yet passed.

Four actual engine-health/recovery scenarios and three generation/startup policy cases passed locally. Engine incidents are reported separately from coordinator liveness and clear only after a later successful graph in the current service generation. The web badge now displays degraded engine health even while its coordinator is alive. Nine actual Chromium locale/health scenarios passed; switching language preserves drafts, bindings and instants while translating validation, port labels, built-in catalog copy and run-history timestamps. Forty-six frontend/catalog/model checks passed. Their reviewed 1440 × 900 captures supplement, rather than replace, assistive-technology trials.

The Oracle cancellation failure was reproduced using real committed SQLite data and an explicit delayed-return barrier with actual n8n. The final cancelled node now atomically settles the run and queued descendants; other active nodes prevent premature completion, and repeated callbacks do not duplicate terminal notifications. Four new cases passed, alongside 13 existing actual retry/cancellation cases and 61 core cases (two explicit fixture skips). The unchanged real Oracle oracle still requires a new CI pass.

Eight compiled native-controller cases passed, including real private-supervisor finish/cancel/dismiss paths. Two active and one queued run drain exactly once; cancellation waits for real worker termination and blocks downstream effects; dismissal preserves the active run and preferences. A reproduced 256 KiB subprocess-pipe deadlock is fixed by draining captured output before waiting for process exit. Modal responses remain injected OS-boundary fixtures; they are not presented-dialog, permission or physical-power evidence. Fresh Swift build, ad-hoc signature verification and side-effect-free self-tests passed, but the final committed-source package must still be rebuilt.

The reviewed mappings currently classify 623 entries as full coverage, 16 partial, 33 manual and 3 superseded. These are coverage classifications, not passing outcomes. This follow-up requires its own same-revision CI and package rebuild; earlier evidence must not be presented as verification of that later revision.

## Previous software checkpoint — 2026-09-18

Verified pushed source: `4179b2950097fc7a7b83304e70a38d441d5a7ff7`. Its [CI run](https://github.com/haowen-ai/sleep-in/actions/runs/35414194898) has completed these jobs:

| Job | Observed result | Scope |
| --- | --- | --- |
| Default regression | 738 passed, 259 skipped | Fixture-dependent cases remain skipped here; do not count them as passes |
| Actual Chromium | 54 passed | Isolated authenticated application and actual n8n execution stories |
| External SQL | 50 passed, 1 failed | Oracle read-only permission was denied with ORA-41900; the test expected only ORA-01031. The narrow assertion correction still requires a new real-Oracle run |
| Compose | Passed | Fresh container build, scheduled graph and exact artifact smoke |
| Workflow integration | 501 passed, 3 skipped | Actual n8n, language/runtime, data, schedule and lifecycle suites; external dialect variants run separately |

The frozen `2516bdb` complete local run finished with **856 passed, 28 fixture skips and 1 failed browser assertion**. All graph and output assertions in that case passed; the final page-content assertion read before asynchronous run details mounted. A bounded wait for the exact Succeeded summary, followed by the unchanged content assertion, passes in an actual Chromium/n8n rerun. This follow-up is not a claim that the frozen run passed.

These jobs overlap; their counts must not be added. Earlier source `2516bdb9970699021523e181ea3c7c4543780bbb` completed **427 integration passes / 3 fixture skips**, but that evidence does not establish a pass for later source. Its browser deadline regression was fixed and the same story passes in the 4179 browser job.

The next candidate adds exact browser branch/locale/sample-retention stories, measured 10/25/50-node graphs, delayed callback replay, native recovery, authenticated fresh-install transfer, backup reference integrity, Oracle/TLS/cancellation boundaries and external-connection browser tests. Reproduced defects fixed in this candidate include a missing expiry notice, 50-node Fit clipping, inverted zoom after fitting a large graph, and known credentials reaching physical worker logs. Logs are now masked before disk writes, including chunk boundaries and final truncation-marker joins. Local scoped results remain development evidence until the new revision's CI results are joined to its reviewed case mappings. Do not combine initial failing development reports with later corrected ones as if they were one release run.

The owner confirmed there is no Apple Developer account or Developer ID certificate. The 4179 Mac preview was built, ad-hoc signature/self-tests passed, and all 64 packaged resources matched its committed source. This does not close signing/notarization, clean-device installation, physical Mac power-state gates, or remaining partial software cases. **Acceptance is incomplete; promotion has not started.**

## Earlier case-by-case acceptance checkpoint — 2026-09-18

The [reviewed trace](traceability/README.md) now maps every original design ID and expands 310 language-pair combinations that the original inventory omitted. There are 674 designed entries, not 674 independent test functions. Exact pytest selectors, remaining assertions, manual gates and superseded interaction cases are recorded separately. CI preserves regression, actual n8n, external SQL and actual Chromium JUnit evidence, then joins it into the `case-by-case-acceptance` artifact. A green test job does not mean `release_ready` is true.

This revision adds authenticated real-graph data oracles, browser editing/retry stories, cross-process schedule contention, database rollback and SMTP uncertainty tests. Reproduced defects include lost-response duplicate admission risk, stranded invalid requests, stale node samples, dropped failure logs, stale retry errors, local Host/Origin validation, missing-driver publication, schedule-bound normalization and unsafe notification retry classification. The admission recovery endpoint resolves an existing run or durably retires its request key, so a delayed original request cannot execute after the user recovers.

Both PRDs now present the current fixed-downward, Mac-first flow consistently. Formal Apple signing/notarization is still unavailable for this preview. Ad-hoc package build/self-tests remain local preview evidence. Physical background/power trials and software cases with partial or missing exact oracles remain open; promotion has not been evaluated as a completed-release activity.

### Checkpoint CI and follow-up verification

Source `857139220eb7b818eed894d6969ace06d7434129` was tested in [the acceptance-branch run](https://github.com/haowen-ai/sleep-in/actions/runs/35387223595). Real n8n integration passed **297 tests with 3 fixture skips**, the PostgreSQL/MySQL/Oracle job passed **32 tests**, the real Chromium job passed **15 tests**, and fresh Compose passed. The complete default regression had **575 passed, 159 skipped, 1 failed**: an older partial-SMTP-delivery assertion expected a retryable failure instead of the new uncertain outcome. This run is therefore **not green and not release-ready**. Counts overlap.

The follow-up preserves the stricter no-duplicate-mail contract: partial delivery is uncertain, cannot retry the entire message, and does not change the business run. All 19 notification/SMTP tests pass locally after updating that old assertion. Additional local acceptance covers mapping, nullable schema validation, duplicate JSON fields, file isolation/expiry, browser inputs and actual n8n orchestration faults. These later changes require their own complete CI revision; the checkpoint result must not be represented as evidence for them.

The next verification candidate adds 10 actual-browser mapping cases and 10 schedule cases, including stale/out-of-order preview responses, explicit expired-once feedback, natural schedule summaries, DST offsets and publication isolation. Six recovery cases use actual owned-process interruption or verified detached workers; cleanup preserves terminal outcome history and does not replay uncertain effects. SQLite cancellation/timeout reports the correct outcome after transaction rollback. JSON guards reject duplicate keys, non-finite values and invalid Unicode before persistence; artifact downloads enforce authorization, retention and safe filenames. These focused suites passed locally; complete regression and CI results must still be associated with this candidate's own source revision.

Reviewed mapping coverage is now **527 full, 112 partial, 33 manual and 2 superseded entries**. These are coverage classifications, not pass counts. The rebuilt Apple-silicon preview passed Swift compilation, ad-hoc signature verification, resource self-test and comparison of all 64 packaged source files. It remains an unnotarized local preview; no login-item registration, power assertion, reboot or external notification was performed by that build.

## Pre-closure acceptance audit — 2026-09-18

Source `47f6e905a142315c0c26267f635d69354bf1949f` has [four successful CI jobs](https://github.com/haowen-ai/sleep-in/actions/runs/35373110451): default regression **396 passed, 96 skipped**; integration **100 passed**; external SQL **29 passed**; clean Compose scheduled graph/artifact/bootstrap checks passed. Counts overlap. At that revision the 364-case design index had no per-case execution trace, and Mac physical/distribution gates remained open. Therefore this was **not complete PRD acceptance or an all-designed-cases-passed claim**. See the [historical audit](ACCEPTANCE-AUDIT-2026-09-18.md).

## Flowchart editing revision — 2026-09-18

Node-list editing, duplication and reviewed deletion; add-next branches; edge insertion; atomic endpoint rewiring; and explicit node-type replacement now have model and DOM coverage. The combined frontend, geometry, fixed-layout and structure suites pass **40 tests**. Tests cover single-action undo, preserved input bindings and edge conditions, cycles/duplicate rejection without partial changes, delete cancellation, type replacement and insertion buttons not starting canvas pan.

Actual Chrome checks on the isolated local preview: edited an edge's source, observed the invalid-input warning, undid it to restore the original edge, inserted a Python node between SQL and Python and observed the three-edge graph, then undid it. These local browser edits were not saved. DeepFOS was inspected only as an interaction reference; a temporary edge change was undone, and Save/Publish were never clicked. This revision changes editor behavior, not the execution engine or earlier hardware-release gates.

## Fixed downward layout revision — 2026-09-18

The latest user requirement supersedes the free-placement acceptance below. The editor now derives positions from dependencies: downward chains, side-by-side branches, merges below predecessors, centered top input and bottom output. Mouse dragging, keyboard repositioning and the horizontal-layout selector are removed. Existing graph semantics and explicit input bindings are preserved. Three new cases failed before implementation; the updated frontend, geometry and fixed-layout suites pass **34 tests**. Earlier four-sided/free-layout evidence below is historical.

## Integrated verification

Local integrated regression before the final build-recovery patch: **443 passed, 26 skipped, 2 dependency deprecation warnings in 474.69 seconds** (exit 0). The 26 skips are 24 external SQL cases, one PostgreSQL storage fixture and one legacy Java-discovery test; the separate 49-language-pair matrix did use a working verified JDK. A second default-fixture run passed 371 tests with 98 explicit skips. The final frontend additions passed 32 UI/geometry tests. The final source commit was then verified by all four GitHub Actions jobs below. The full local run enables actual n8n 2.39.7, Node, JDK, Maven, Gradle and npm fixtures. The development Mac has no Docker daemon; clean Compose and external SQL fixtures run separately in GitHub Actions.

Component evidence already obtained:

- Real n8n concurrency, retries, artifacts, cancellation and schedules: 13 passing cases. Independent roots have overlapping measured execution intervals; this was originally a reproduced failure.
- All **49 ordered language pairs**, plus seven actual n8n fan-out graphs covering those 49 edges. SQL in this matrix is SQLite. Tests preserve null, decimal text, integer, empty string, Chinese and emoji; receivers actually read the input file.
- Runtime packs: real hash-locked Python wheel, npm dependency install, source/JAR/Maven/Gradle builds and C/C++ multi-source builds. Frozen versions reject tampering rather than silently changing a publication.
- Real loopback SMTP tests exercise exact recipients, secret masking, partial refusal and failure isolation. Local HTTP fixtures exercise webhook delivery. No notification was sent to an external recipient.
- Mac package: Swift compilation, resource self-test and ad-hoc code-signature verification. Isolated fixtures exercise update validation, backup, stop/start exclusion and rollback.

## Historical GitHub Actions verification — source 38dcfb6

[All four jobs passed](https://github.com/haowen-ai/sleep-in/actions/runs/35322543880) for source commit `38dcfb65f09e08e0c3ada8cadc9f2d21998ddf53`:

| Job | Result |
| --- | --- |
| Full Linux regression with PostgreSQL storage | 388 passed, 96 explicit fixture skips, 2 dependency warnings |
| Actual n8n / language / supervisor integration | 100 passed, 2 dependency warnings |
| PostgreSQL, MySQL and Oracle fixtures | 29 passed, no skips; includes 24 live database cases |
| Fresh Docker Compose | Scheduled SQL → Python → JavaScript graph, native n8n execution ID, exact output artifact and repeated-bootstrap singleton verified |

These suites overlap; the counts must not be added as a unique-test total. The first CI attempt caught a machine-specific Node test path and Oracle fresh-DDL read-consistency timing. Test setup was corrected and rerun without weakening production read-only SQL behavior. Interrupted runtime builds also have lock-owner/child-process recovery tests (32 runtime tests plus seven actual project builds passed locally). The final Apple-silicon app was rebuilt, ad-hoc signature checked and resource self-tested; no OS installation or permission change was invoked.

## Actual browser checks

Chrome, 2026-09-17, isolated localhost port 8767:

- Reproduced the reported diagonal layout by moving SQL below/right of Python and JavaScript below/left. Four-sided routes now point into distinct hollow input ports, leave filled output ports and avoid the cards. The two paths no longer share an ambiguous endpoint.
- Ran the actual SQL → Python → JavaScript flow and observed all three nodes succeed (`5d229d3cee534e66e08a2912`).
- Completed the template/time/readiness journey. Changed time to 08:15 and timezone to America/Chicago, switched to Chinese without losing either, and observed successful test → publication → enabled schedule with five upcoming Mondays. The guided test run is `af0e189fe861…`.
- At a real 1024 × 768 viewport, editor navigation and selected-node inspector remained available without page-wide overflow.
- At 390 × 844, opened navigation and run history. Page width equalled viewport width (390); the table scrolls within its own panel. Full phone graph authoring is not advertised. Temporary viewport override was reset.
- Original preview/user drafts were kept separate from these test records.

## Historical PRD acceptance trace (not current release approval)

| Criterion | Software and evidence | Release disposition |
| --- | --- | --- |
| A01 | Bilingual catalog parity, DOM draft-preservation tests and actual time/zone switching | Browser verified |
| A02 | Language catalog; SQL dialect selector and connection forms | Implemented/tested |
| A03 | Exact SQL → Python → JS summary and artifact, native n8n | Actual execution verified |
| A04 | Schema/reference validation, empty rows, missing versus null and explicit defaults | Automated |
| A05 | Complete large dataset spill, checksummed artifact materialization, exact 12,000-row transfer | Actual n8n tests |
| A06 | Stable IDs, rename/delete/broken mapping, undo | Model/DOM tests |
| A07 | Seven-language matrix, real compiler/JAR/project/dependency builds | Verified on development Mac |
| A08 | SQLite real transactions; 24 external PostgreSQL/MySQL/Oracle cases and pinned fixtures | All 24 live cases passed in CI |
| A09 | Branch terminal markers, optional defaults, merges, measured parallel roots | Actual n8n tests |
| A10 | Form schedules, once/weekday/month-end/DST/zone, persisted occurrences | Automated plus real scheduled occurrence |
| A11 | Immutable publication/environment/source versions and admitted snapshots | Automated |
| A12 | Duplicate callback/admission, deadline/cancel, process-identity-checked crash cleanup | Actual subprocess and n8n tests |
| A13 | Independent notification outbox, tests off, masked payloads, exact recipient/partial refusal | Loopback SMTP/HTTP verified |
| A14 | Portable source-inclusive exports remove environment/connection credentials; imports require rebind | Automated |
| A15 | Earlier free-direction preview checks; current PRD uses fixed downward layout and has separate case mappings | Historical browser evidence only; use current UI-G/UI-L mappings |
| A16 | Compose includes native v2 orchestrator, checks real schedule/execution ID/artifact | Fresh Compose CI passed |
| A17 | GUI progress, private runtime bootstrap, repeated-start singleton | Build/fixture tested; clean non-developer Mac still required |
| A18 | Visible fresh-local credentials, change hides card/revokes sessions, restored/external guards | Automated; fresh-local browser login verified |
| A19 | Always-on AC/battery idle-sleep request; no browser-dependent lifetime | Code/isolated lifecycle tested; physical lock/power trial still required |
| A20 | Daily recurrence persistence, explicit-stop preference, conservative recovery | Automated time/lifecycle tests; two-day unplug/replug trial still required |
| A21 | SMAppService permission states, signed update verification, health rollback and token-scoped update lock | Fixture/build tested; Developer ID/notarization and real OS permission/login trials required |
| A22 | Three-page guided template, form schedule, mapping picker and low-code branch form | Actual guided journey; authoring model/DOM tests |
| A23 | Zero/paused jobs preserve supervisor; explicit full-stop lifecycle; battery telemetry | Isolated tests; measured battery/overnight soak still required |

## External gates cannot be replaced with unit tests

The repository does not contain the owner's Developer ID/notarization credentials. No signing identity was invented and no macOS permission or power setting was changed during verification. A real clean-Mac install, permission grant/revocation, logout/reboot, locked-screen AC/battery trials and measured multi-day soak require a designated test machine and elapsed time. The implementation includes these paths and a [physical test protocol](local-lifecycle-cases.md), but they are not marked passed.

Current native installation targets Apple silicon macOS 13+. A test on this development Mac does not certify every older macOS release or Intel machines. Native execution uses the current account and does not provide container-level CPU/memory isolation.

## Reproduction

```sh
pip install -r requirements-dev.txt
python -m pytest -q -ra
SLEEP_IN_EXTERNAL_SQL_TESTS=1 python -m pytest -q tests/test_workflow_external_sql.py
```

The external command requires the [isolated SQL fixtures](external-sql-matrix.md). Real-engine tests require both `SLEEP_IN_N8N_COMMAND` and `SLEEP_IN_TEST_N8N_COMMAND` as JSON argv arrays of absolute Node/n8n paths. The language matrix requires `SLEEP_IN_TEST_JAVAC`; project/dependency tests accept `SLEEP_IN_TEST_NPM`, `SLEEP_IN_TEST_MAVEN`, `SLEEP_IN_TEST_GRADLE`. Mac supervisor/package fixtures use `SLEEP_IN_TEST_INSTALL_DIR` and `SLEEP_IN_TEST_NATIVE_APP`. Every missing fixture has an explicit skip reason.
