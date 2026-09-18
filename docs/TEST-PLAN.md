# Sleep In v2 test design

English · [简体中文](TEST-PLAN.zh-CN.md)

**Status: designed, not an execution report.** Feature work is paused while the test contract is established. Existing implementation drafts are preserved; they do not define expected behavior. Use synthetic data and isolated state only.

Sleep In must prove visual orchestration of complex scheduled workflows, not merely a timer around one script. Every case specifies priority, preconditions, concrete inputs, steps, expected results and evidence. P0 blocks delivery of the affected capability; P1 gates its inclusion in the complete release. Unavailable toolchains/databases are environment-blocked, never silently passed.

## Test suites

- [Data transfer, language pairs and graph execution](testing/workflow-data-cases.md)
- [Schedules, timezones, recurrence and duplicate admission](testing/schedule-cases.md)
- [Canvas, mapping, publication and bilingual interaction](testing/workflow-ui-cases.md)
- [Mac installation, accounts and always-on lifecycle](testing/local-lifecycle-cases.md)
- [Publication, security, persistence and recovery](testing/publication-security-cases.md)

The [complete Chinese test protocol](TEST-PLAN.zh-CN.md) includes fixtures, golden paths, evidence requirements and development gates. Detailed cases use bilingual or Chinese labels for user review; the application remains English-first.

## Binding semantics

Edges establish execution order; mappings independently select data. A connected Python node may consume SQL rows, constants, workflow parameters, another reachable node's output, or an empty input object. It never implicitly inherits its predecessor's output. Not consuming output does not mean ignoring a predecessor failure. Optional missing inputs, intentionally unbound inputs and null/false/zero/empty values are separate cases.

Three SQL rows must reach Python as one complete array in one invocation. Python produces `summary: {count: 3, total: "30.75"}`; JavaScript receives the same typed object and writes a verifiable artifact. stdout is a log, not structured output. References resolve relative to output.data. High-precision values and unsafe JavaScript integers remain schema-marked strings. Published runs retain immutable graph/runtime/input snapshots.

## Golden-path gate

Before building more UI, create failing tests for SQL → Python → JavaScript, intentionally unused upstream output, no input, mixed input sources, empty rows, optional missing versus required missing, type fidelity, timezone preview and duplicate admission. Then implement and verify each behavior. Add fan-out/joins, skipped branches, failure/retry/cancellation and uncertain side effects before claiming complex orchestration.

Unit tests use independently fixed expected timestamps. Real subprocess tests prove language exchange. Database integration tests prove dialect behavior. A real n8n run proves orchestration. Browser tests prove drag/drop and persistence. Physical Mac power/lock tests prove background behavior. These evidence levels are not interchangeable.

## Evidence and release gates

Record case ID, revision, environment/version, fixture, expected/actual output, status, run/node/attempt IDs and sanitized evidence. Allowed statuses: DESIGNED, RED, PASS, FAIL, BLOCKED_ENV, NOT_RUN. A test definition, HTTP 202 or exit code 0 is not a pass.

Do not interrupt the user's active Mac by locking, rebooting, changing login items or forcing power transitions as incidental tests. Mark physical trials pending until a dedicated test window is available. Never publish secret-bearing logs. No release may claim supported Oracle/Java/C, signed installation, battery duration or 24/7 uptime from icons, mocks or an unexecuted matrix.

[Contract decisions that fix expected behavior](testing/contract-decisions.md)

Machine-readable case inventory: [364 cases](testing/case-index.json).
