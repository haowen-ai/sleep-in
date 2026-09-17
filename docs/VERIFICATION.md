# Verification record

This document reports evidence, not release aspirations.

## Current environment

- Development host: Apple Silicon macOS, Python 3.12.
- Docker Engine/Desktop is not installed on the development host. Full Compose behavior must be established independently in CI.
- UI tests use only synthetic data on a separate localhost instance. No production tasks are invoked.

## Automated coverage

Tests cover schedule DST gaps/folds, short months, interval anchors, setup/login/CSRF/roles, session revocation, immutable run snapshots, overlap/deduplication, safe archives, runtime behavior, cancellation/timeouts, Unicode logs, artifacts, backup/restore and translation behavior.

The GitHub CI workflow runs the test suite plus a disposable Compose deployment that checks n8n's actual heartbeat, a scheduled Python execution and output download, and idempotent workflow import. A configured CI workflow is not itself evidence of a passing run; inspect the Actions result for the commit you use.

## Observed integration results — 2026-09-17

- The [first Compose job](https://github.com/haowenchen0811/n8n-task-console/actions/runs/35281424065/job/105404011645) passed on Ubuntu: fresh containers, PostgreSQL, automatic n8n initialization, an actual scheduled execution, exact output-file content, and repeated workflow initialization without duplication.
- An isolated native n8n 2.39.7 instance on macOS sent real five-second heartbeats. A browser-created one-minute task repeatedly succeeded, showing stdout and its output in the run detail page.
- The artifact API returned HTTP 200, the exact 39-byte expected file and an attachment header. The local browser's download navigation was blocked by its client environment; that specific browser download path is not claimed as passed.
- Browser checks confirmed English sign-in, task creation, and preservation of unsaved task fields when switching between English and Chinese.
- CI also caught a Python invocation-path issue and a test that incorrectly assumed a filesystem traversal order. Both were corrected; the workflow invokes `python -m pytest` and explicitly installs Node for frontend tests. Refer to the latest CI result for the commit you use.

## Not yet established

- Windows Docker Desktop/WSL2 and local macOS Docker end-to-end behavior.
- Representative production load, P95 latency, minimum resource sizing, or a three-person onboarding study.
- Formal security audit and long-running unattended operation.
- Prebuilt release image availability until the release workflow successfully publishes a tag.

The README therefore calls this an early development release, not a production-certified platform.
