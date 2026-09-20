# Verification record

Current completion work is tracked in [full verification ledger](testing/COMPLETE-RESULTS.md). The earlier counts below are historical, not the latest full-suite result.
This document reports evidence, not release aspirations.

The sections below preserve the **v1** verification history. Current visual-workflow and native Mac preview evidence is maintained in [Workflow preview verification](testing/RESULTS.md); v1 passing results do not establish v2 behavior.

## Current environment

- Development host: Apple Silicon macOS, Python 3.12.
- Docker Engine/Desktop is not installed on the development host. Full Compose behavior must be established independently in CI.
- UI tests use only synthetic data on a separate localhost instance. No production tasks are invoked.

## Automated coverage

The final local suite passed **80 tests**, with one PostgreSQL-only test skipped because local PostgreSQL was not configured. The [Linux test job](https://github.com/haowen-ai/sleep-in/actions/runs/35282367674) passed with PostgreSQL configured.

Tests cover schedule DST gaps/folds, short months, interval anchors, setup/login/CSRF/roles, session revocation, immutable run snapshots, overlap/deduplication, safe archives, runtime behavior, cancellation/timeouts, Unicode logs, artifacts, backup/restore and translation behavior.

The GitHub CI workflow runs the test suite plus a disposable Compose deployment that checks n8n's actual heartbeat, a scheduled Python execution and output download, and idempotent workflow import. A configured CI workflow is not itself evidence of a passing run; inspect the Actions result for the commit you use.

## Observed integration results — 2026-09-17

- The [first Compose job](https://github.com/haowen-ai/sleep-in/actions/runs/35281424065/job/105404011645) passed on Ubuntu: fresh containers, PostgreSQL, automatic n8n initialization, an actual scheduled execution, exact output-file content, and repeated workflow initialization without duplication.
- An isolated native n8n 2.39.7 instance on macOS sent real five-second heartbeats. A browser-created one-minute task repeatedly succeeded, showing stdout and its output in the run detail page.
- The artifact API returned HTTP 200, the exact 39-byte expected file and an attachment header. The local browser's download navigation was blocked by its client environment; that specific browser download path is not claimed as passed.
- Browser checks confirmed English sign-in, task creation, preservation of unsaved task fields when switching languages, immediate translated navigation/status labels, and a manual execution changing from queued to succeeded with its final logs and artifact shown automatically.
- CI also caught a Python invocation-path issue and a test that incorrectly assumed a filesystem traversal order. Both were corrected; the workflow invokes `python -m pytest` and explicitly installs Node for frontend tests. Refer to the latest CI result for the commit you use.

## Sleep In interface refresh — 2026-09-17

- The local suite again passed 80 tests, with the PostgreSQL-only test skipped locally.
- Browser checks confirmed the redesigned task list, task form and sign-in page; switching languages retained entered task names and translated script choices, schedule options and preview dates. Signing out and back in succeeded without application console errors.
- The Python wheel was built and inspected to confirm the bundled icons and their license are included.
- Responsive CSS is included, but the browser viewport override did not take effect on this host; a phone-sized visual check is not claimed.
- Product and repository names changed to Sleep In / 不再早起 (`sleep-in`). The internal Python package and Compose project identifier remain stable for existing installations.

## Not yet established

- Windows Docker Desktop/WSL2 and local macOS Docker end-to-end behavior.
- Representative production load, P95 latency, minimum resource sizing, or a three-person onboarding study.
- Formal security audit and long-running unattended operation.
- Prebuilt release image availability until the release workflow successfully publishes a tag.

The README therefore calls this an early development release, not a production-certified platform.
