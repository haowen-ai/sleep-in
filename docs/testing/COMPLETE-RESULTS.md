# Visual workflow completion and release gates

This ledger supersedes the earlier [core-preview results](RESULTS.md). Tests use synthetic, isolated state. The original test design was committed before implementation; later defects were reproduced with failing tests before fixes. Passing software tests do not establish physical power or public-distribution behavior.

## Additional software closure — 2026-09-18

This candidate is based on `2516bdb9970699021523e181ea3c7c4543780bbb`. That baseline's [CI run](https://github.com/haowenchen0811/sleep-in/actions/runs/35412859958) passed the default regression (**681 passed, 204 skipped**), external SQL (**32 passed**) and clean Compose, but its browser job had **34 passed, 1 failed**. The SQLite ordering story exceeded its deadline because cancellation checks repeatedly opened store transactions. The unchanged two-million-row browser fixture passes locally after throttling that expensive probe; the new candidate still needs its own CI evidence.

The candidate also fixes permanent-output retries, uncertain SQL commit replay, child-process cleanup after the parent exits, unbounded script log files, stalled engine startup deadlines, unavailable-engine queues, operator editor access, lost-request recovery with browser storage disabled, persistent validation messages and required schedule anchors. Schedule history now exposes skipped ranges and links admitted occurrences. Explicit reruns of uncertain effects require a visible decision in both languages.

Focused evidence includes **69 frontend/layout/report tests**, **32 browser-feedback/frontend tests**, the separate actual double-click/storage-disabled run (**1 passed**), native cancellation/retry graphs, database transaction fault injection and browser execution stories. These suites overlap and are not a complete same-revision pass count. Reviewed coverage is **581 full, 58 partial, 33 manual, 3 superseded** across **675 entries**; full is coverage, not execution status. CI must join fresh results for the source commit before acceptance is assessed.

The owner confirmed there is no Apple Developer account or Developer ID certificate. A local ad-hoc build cannot close signing/notarization, clean-device installation or physical Mac power-state gates. No promotion decision has been made.

## Case-by-case acceptance closure — 2026-09-18

The [reviewed trace](traceability/README.md) now maps every original design ID and expands 310 language-pair combinations that the original inventory omitted. There are 674 designed entries, not 674 independent test functions. Exact pytest selectors, remaining assertions, manual gates and superseded interaction cases are recorded separately. CI preserves regression, actual n8n, external SQL and actual Chromium JUnit evidence, then joins it into the `case-by-case-acceptance` artifact. A green test job does not mean `release_ready` is true.

This revision adds authenticated real-graph data oracles, browser editing/retry stories, cross-process schedule contention, database rollback and SMTP uncertainty tests. Reproduced defects include lost-response duplicate admission risk, stranded invalid requests, stale node samples, dropped failure logs, stale retry errors, local Host/Origin validation, missing-driver publication, schedule-bound normalization and unsafe notification retry classification. The admission recovery endpoint resolves an existing run or durably retires its request key, so a delayed original request cannot execute after the user recovers.

Both PRDs now present the current fixed-downward, Mac-first flow consistently. Formal Apple signing/notarization is still unavailable for this preview. Ad-hoc package build/self-tests remain local preview evidence. Physical background/power trials and software cases with partial or missing exact oracles remain open; promotion has not been evaluated as a completed-release activity.

### Checkpoint CI and follow-up verification

Source `857139220eb7b818eed894d6969ace06d7434129` was tested in [the acceptance-branch run](https://github.com/haowenchen0811/sleep-in/actions/runs/35387223595). Real n8n integration passed **297 tests with 3 fixture skips**, the PostgreSQL/MySQL/Oracle job passed **32 tests**, the real Chromium job passed **15 tests**, and fresh Compose passed. The complete default regression had **575 passed, 159 skipped, 1 failed**: an older partial-SMTP-delivery assertion expected a retryable failure instead of the new uncertain outcome. This run is therefore **not green and not release-ready**. Counts overlap.

The follow-up preserves the stricter no-duplicate-mail contract: partial delivery is uncertain, cannot retry the entire message, and does not change the business run. All 19 notification/SMTP tests pass locally after updating that old assertion. Additional local acceptance covers mapping, nullable schema validation, duplicate JSON fields, file isolation/expiry, browser inputs and actual n8n orchestration faults. These later changes require their own complete CI revision; the checkpoint result must not be represented as evidence for them.

The next verification candidate adds 10 actual-browser mapping cases and 10 schedule cases, including stale/out-of-order preview responses, explicit expired-once feedback, natural schedule summaries, DST offsets and publication isolation. Six recovery cases use actual owned-process interruption or verified detached workers; cleanup preserves terminal outcome history and does not replay uncertain effects. SQLite cancellation/timeout reports the correct outcome after transaction rollback. JSON guards reject duplicate keys, non-finite values and invalid Unicode before persistence; artifact downloads enforce authorization, retention and safe filenames. These focused suites passed locally; complete regression and CI results must still be associated with this candidate's own source revision.

Reviewed mapping coverage is now **527 full, 112 partial, 33 manual and 2 superseded entries**. These are coverage classifications, not pass counts. The rebuilt Apple-silicon preview passed Swift compilation, ad-hoc signature verification, resource self-test and comparison of all 64 packaged source files. It remains an unnotarized local preview; no login-item registration, power assertion, reboot or external notification was performed by that build.

## Pre-closure acceptance audit — 2026-09-18

Source `47f6e905a142315c0c26267f635d69354bf1949f` has [four successful CI jobs](https://github.com/haowenchen0811/sleep-in/actions/runs/35373110451): default regression **396 passed, 96 skipped**; integration **100 passed**; external SQL **29 passed**; clean Compose scheduled graph/artifact/bootstrap checks passed. Counts overlap. At that revision the 364-case design index had no per-case execution trace, and Mac physical/distribution gates remained open. Therefore this was **not complete PRD acceptance or an all-designed-cases-passed claim**. See the [historical audit](ACCEPTANCE-AUDIT-2026-09-18.md).

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

[All four jobs passed](https://github.com/haowenchen0811/sleep-in/actions/runs/35322543880) for source commit `38dcfb65f09e08e0c3ada8cadc9f2d21998ddf53`:

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

## PRD acceptance trace

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
| A15 | Free-direction UI, keyboard/model tests, 1024 and 390 actual viewport checks | Browser plus tests; no claim of user study |
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
