# Verification record

This document reports evidence, not release aspirations.

## Current environment

- Development host: Apple Silicon macOS, Python 3.12.
- Docker Engine/Desktop is not installed on the development host. Full Compose behavior must be established independently in CI.
- UI tests use only synthetic data on a separate localhost instance. No production tasks are invoked.

## Automated coverage

Tests cover schedule DST gaps/folds, short months, interval anchors, setup/login/CSRF/roles, session revocation, immutable run snapshots, overlap/deduplication, safe archives, runtime behavior, cancellation/timeouts, Unicode logs, artifacts, backup/restore and translation behavior.

The GitHub CI workflow runs the test suite plus a disposable Compose deployment that checks n8n's actual heartbeat, a scheduled Python execution and output download, and idempotent workflow import. A configured CI workflow is not itself evidence of a passing run; inspect the Actions result for the commit you use.

## Not yet established

- Windows Docker Desktop/WSL2 and local macOS Docker end-to-end behavior.
- Representative production load, P95 latency, minimum resource sizing, or a three-person onboarding study.
- Formal security audit and long-running unattended operation.
- Prebuilt release image availability until the release workflow successfully publishes a tag.

The README therefore calls this an early development release, not a production-certified platform.
