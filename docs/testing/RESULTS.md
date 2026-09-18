# Workflow preview verification

Current completion work is tracked in [full verification ledger](COMPLETE-RESULTS.md). The earlier counts below are historical, not the latest full-suite result.
English · [中文测试总纲](../TEST-PLAN.zh-CN.md)

The acceptance design was committed before implementation on 2026-09-17 (`ee97bbc`). It contains **364 concrete case rows**, plus the **49 ordered language-pair matrix**. These are design coverage, not a claim that 364 acceptance cases or all 49 pairs passed. `case-index.json` remains the design inventory; the evidence below records what was actually executed.

## Executed evidence

The integrated suite includes retained v1 tests as well as new workflow, SQL, scheduler, UI-model, transfer and local-service tests. Fresh full regression on 2026-09-17: **267 passed, 2 skipped, 2 dependency deprecation warnings in 83.48 seconds** (exit 0). The two skips are the isolated PostgreSQL fixture and a working Java toolchain. Tests use private temporary state and synthetic data. Real n8n and real native subprocesses are explicitly enabled for integration verification; physical power assertions and login registration are not.

| Scope | Evidence |
| --- | --- |
| Real n8n execution | Imported and executed SQL → Python → JavaScript graph; exact summary total `30.75`, three records, exact report bytes, one attempt per node and persisted n8n execution identity |
| Dependency without data | Actual n8n branch/merge test and browser-created Python → JavaScript workflow; downstream input is `{}` unless explicitly mapped |
| Input semantics | Constants, workflow parameters, selected paths, optional missing defaults, present null/false/zero/empty values, unreachable sources and cycles |
| Join semantics | Required failure blocks; optional failed predecessor can produce a partial run; normal skipped branches can join; any/all wait for terminal markers |
| Scheduling | Fixed time oracles for interval/daily/weekdays/weekly/monthly/once, leap years/timezones/DST; genuine real-clock occurrence; durable occurrence retry and deduplication |
| SQL boundaries | Parameter binding, quoted/comment placeholders, precision, read-only connection enforcement, multi-statement rollback, output-schema/quota failures before commit |
| Recovery | Cancellation during orchestration, duplicate node callbacks, uncertain interrupted work, terminal result preservation, supervisor generation/health, finish/cancel stop |
| Runtime protocol | Python/JavaScript, Shell, C and C++ actual execution; Java requires an available JDK and was not verified here |
| Template transfer | Admin/CSRF API protection; portable source-inclusive export, instance configuration removed, new disabled draft on import, execution metadata injection rejection |
| UI | Actual card/library/port drag, undo, cycle rejection, Chinese switch retaining unsaved code, constant mapping, weekly preview and successful real runs |
| Mac package | Native companion compilation, ad-hoc signature and resource/termination-policy self-test; isolated runtime bootstrap and real supervisor lifecycle |

Detailed reports: [backend](backend-results.md), [browser](browser-results.md), [frontend models](frontend-results.md), [local lifecycle](local-results.md).

## Reproduce

Install the development requirements, provide Node on `PATH` or via `NODE`/`SLEEP_IN_NODE`, then run:

```sh
python -m pytest -q -ra --tb=short
```

Real-engine tests require `SLEEP_IN_N8N_COMMAND` and `SLEEP_IN_TEST_N8N_COMMAND`, each a JSON argv array of absolute Node and installed n8n CLI paths. The prepared-installer and native policy tests additionally use `SLEEP_IN_TEST_INSTALL_DIR` and `SLEEP_IN_TEST_NATIVE_APP`. Without those fixtures their skips are explicit; a default test invocation does not establish real engine or Mac lifecycle coverage. PostgreSQL tests require the isolated `POSTGRES_TEST_URL` fixture.

CI now includes a separate real-n8n integration job. A local passing run does not establish the remote CI result.

## Still open

- The entire PRD is not implemented: immutable runtime/dependency packs and their installers, isolated single-node testing, artifact-as-input contracts, notifications, automatic retry policies, retention/migration/upgrade recovery and further low-code conveniences remain development work.
- PostgreSQL/MySQL/Oracle adapters need live database-specific verification; no production database was contacted. Java has no working JDK in the verified local environment. Intel Mac distribution remains unverified.
- Clean-Mac experience, Developer ID signing/notarization, real macOS approval paths, physical lock/unplug/battery/lid trials and multi-day soak remain manual release gates. No global power setting or login item was changed by this verification.
- Battery operation describes the idle-sleep assertion policy, not unlimited battery life or operation after shutdown. Scripts run with the signed-in user's permissions and are not sandboxed against hostile code.
- Export deliberately includes author-written source and literal values. Review them before sharing; configured credentials being filtered cannot detect arbitrary secrets pasted into source code.

This is a development preview and a test-first implementation baseline, not a completed general release.
