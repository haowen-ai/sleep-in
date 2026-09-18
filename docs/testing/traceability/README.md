# Reviewed acceptance trace

The original `../case-index.json` is a design inventory, not a passing-test list. Its 364 cases are mapped by `data.json`, `schedule-security.json`, `ui.json` and `lifecycle.json`.

`data-pair-design.json` explicitly expands another 310 combinations that were specified in the data design but missing from the original inventory: baseline, order-only, empty, types, failed predecessor and artifact language pairs, plus external database directions. `data-pairs.json` maps these exact oracles. There are **674 designed entries**, not 674 independent pytest functions; batched real graphs may cover multiple entries.

Every mapping includes exact pytest selectors, reviewed coverage and specific remaining assertions. `full` describes test coverage, **not a passing outcome**. Actual result files determine passed, skipped or failed. Partial coverage cannot become a pass even when its referenced unit tests succeed. Superseded entries preserve the user's replacement requirement; they are never counted as passing tests. Manual means physical/operator evidence, not an excuse for unwritten automated tests.

Generate fresh JUnit output from the complete regression, actual engine/language fixtures, external database fixtures and real browser tests. The CI workflow preserves individual XML artifacts. Keep results tied to the tested revision; never mix pre-fix and post-fix runs to hide a failure.

CI's `acceptance-evidence` job downloads those reports from the same workflow run and uploads `case-by-case-acceptance`, including the source revision. Successful report generation only means that evidence was joined; inspect its explicit `release_ready` field and remaining cases before making any completion claim.

```sh
python tools/acceptance_report.py \
  --cases docs/testing/case-index.json \
  --cases docs/testing/traceability/data-pair-design.json \
  --mapping docs/testing/traceability/data.json \
  --mapping docs/testing/traceability/data-pairs.json \
  --mapping docs/testing/traceability/schedule-security.json \
  --mapping docs/testing/traceability/ui.json \
  --mapping docs/testing/traceability/lifecycle.json \
  --junit regression-results.xml \
  --junit integration-results.xml \
  --junit external-sql-results.xml \
  --junit browser-results.xml \
  --output-dir acceptance-results --require-complete
```

`--require-complete` exits nonzero unless every current designed case has complete passing evidence and the supplied regression has no failures. Independent fixture execution may resolve a skip for the **same exact test instance**; it does not resolve other missing parameters or cases. Any failure remains a failure. The JSON preserves test-level evidence and hashes of the input reports/mappings; the Markdown provides a per-case review table.

The generator is an evidence join, not a proof of coverage by itself. Reviewers must verify that every mapped assertion matches the case's complete oracle and required execution level. A restricted `-k`/parameter run must not be passed off as a complete suite. Hardware and signing gates still need their designated procedures; the current automated reporter deliberately cannot turn policy mocks into physical acceptance.
