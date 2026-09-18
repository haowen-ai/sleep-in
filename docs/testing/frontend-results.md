# Workflow frontend verification

Date: 2026-09-17. Scope: current development checkout, not a released/signed application.

## Automated checks executed

Command (using bundled Node and the isolated Python virtual environment):

```sh
NODE=/Users/ec/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/bin/node \
/Users/ec/Documents/Codex/2026-09-17/cha/work/taskconsole-venv/bin/python \
-m unittest tests.test_frontend tests.test_workflow_frontend -q
```

Latest result: **25 tests passed** (9 pre-existing frontend checks + 16 workflow checks). This proves the listed pure-model/catalog/syntax behavior, not browser interactions or successful n8n execution.

| Behavior | Evidence | Related designed cases |
|---|---|---|
| Explicit node bindings create dependencies and reject cycles without partial mutation | Real GraphModel tests | UI-G04/05, UI-M01 |
| Rename keeps IDs, deletion exposes broken references, undo/redo restores graph | Real GraphModel tests | UI-G08, UI-M10/11 |
| Duplicate deep-copies data, retains incoming dependencies, creates no outgoing consumers | Real GraphModel tests; missing incoming-copy behavior first failed before change | UI-G07 |
| Drag movement is one undoable position update | Real GraphModel move/undo test; move API initially missing and failed | UI-G03/08 |
| Empty mappings receive no automatic upstream data; removing mappings keeps dependency edges | RED missing preview/unbind APIs → GREEN real model test | UI-M02/03 |
| Constants, parameters and upstream rows coexist; optional missing uses default while present null remains null | RED missing preview API → GREEN input-resolution preview test | UI-M04/05/08 |
| Required missing input fails instead of silently becoming null | Real preview resolver assertion | UI-M06 |
| Field picker uses path token arrays for literal dotted keys, nested keys and explicit array indexes | RED missing outputOptions → GREEN real option/path tests | C06, UI-M01/12 |
| Query SQL does not suggest write-only affectedRows | RED missing outputOptions → GREEN test | SQL output contract |
| Explicit English/Chinese catalogs have identical keys; literal labels resolve; modules parse; no unsafe HTML insertion | Catalog and syntax checks | UI-L01 |
| Locale refresh updates labels/attributes without replacing editor input values | DOM-independent locale updater test; does not substitute for actual browser check | UI-L02/03 |
| Named scheduled triggers start disabled with independent schedules and inherited explicit timezone | RED missing helper → GREEN model test | UI-S02/06, SCH-040 |
| Workflow routes select workflow service health; classic routes retain classic scheduler | RED missing helper → GREEN model test | Health truthfulness |
| Save acknowledgments preserve live form references and retain dirty state for edits made during request | RED missing helper → GREEN model test | Draft preservation |
| Library pointer drop respects zoom/scroll, suppresses duplicate click, and cancels outside/canceled gestures | RED missing pointer handler → GREEN real handler test with event-target fixture | UI-G01 |

The original six workflow checks were first run before the model/module existed and failed. Subsequent behavioral batches were run failing before implementing their missing methods. Browser defects in CSP positions, collapsed navigation names and dirty-status localization were reported by root QA and fixed; live rechecks are owned by root.

## Implemented interface surface

- Workflows default route; classic task/script/run routes retained.
- Workflow/template list and real server-backed template instantiation.
- Language-only library; pointer drag/drop insertion; pointer card repositioning; port connections; selection/outline; undo/redo; duplication/deletion; zoom/fit/layout.
- Configuration inspector, editable source, Python/JS function versus file mode, SQL dialect/connection/query/write mode, Java main class, actual runtime profiles, join/error settings.
- Explicit constant/parameter/upstream field mapping; optional fallback separately configured from optional control-edge failure; token paths; schema editing; run-attributed examples and stale-sample indication.
- Draft save, publish, draft test, published run; actual backend statuses, node inputs/outputs/logs/attempts and artifact download; cancellation requests.
- Form schedules without Cron; multiple named scheduled triggers via workflow.triggers, independent parameters/timezones, follow current publication or pin current known version.
- Connections create/test; runtime availability and capability inspection from server responses.
- English/Chinese in-place label updates preserve editor source/graph buffers. Default local credential card uses server-provided values only; password change clears stale local-account data and refreshes bootstrap.
- Workflow service health refreshes every 10 seconds without recreating the editor.

## Integration boundaries and remaining evidence

Actual browser drag/drop/port events, accessibility, narrow layouts, live schedule execution, per-language runtime compatibility and n8n execution require their dedicated tests. Pure model checks do not pass those acceptance cases. Root is performing browser/integration verification against the isolated loopback QA app.

The current backend does not yet expose all PRD operations: named manual/API trigger admission, operator-only trigger editing, immutable runtime installation/version management, notifications, node tests/retry controls, workflow import/export and source project uploads. The frontend does not present static pretend installation/results controls for those operations. Their full PRD requirements remain open.

External database drivers/toolchains can be unavailable; runtime UI reports actual server status/reason and publication remains the readiness gate. Tests may have real side effects; the interface describes them as real code execution.
