# Local Mac verification results

This records execution evidence, separately from the [56-case design](local-lifecycle-cases.md). **No physical battery/lock/sleep/reboot trial or login-item registration has been performed.** Local artifacts are development previews, not notarized releases.

| Check / related cases | Environment and actual result | Status / limit |
|---|---|---|
| Account initialization policy, partial MAC-AU-01/02/04/05 | `tests/test_local_accounts.py`: 9 tests pass on temporary SQLite stores; explicit local/loopback required; original hash works; changed hash hides card; pre-existing user/script/version/workflow/task/connection suppress initialization | PASS for tested policy; root API/session tests and clean-install provenance remain separate |
| Lock and lifecycle policy, partial MAC-IN-07, MAC-BG-08/09/12, MAC-RC-10 | `tests/test_local_service.py`: single-instance lock, stopped preference, stale PID and previous-generation rejection, fixed local child environment, explicit registration requirement, fresh worker heartbeat, parent-pipe EOF process cleanup, unknown launch argument rejection, draining-before-stop | PASS: 12 automated tests (one requires the explicitly selected prepared installation fixture); no actual OS assertion/native registration involved |
| Native wrapper build, partial MAC-IN-09 | Swift 6.4 on macOS 26.6.2 (25G83), Apple silicon, compiled macOS 13 target; `SleepIn-preview-local-v4.app` ad-hoc signing, `codesign --verify --deep --strict` and `--self-test` succeeded. Test validated executable resource path only | PASS build/self-test; NOT_RUN GUI permission lifecycle; BLOCKED_ENV Developer ID/notarization |
| Download/bootstrap trial | Isolated `work/local-bootstrap-verification`, explicit `--install-only`; pinned uv/Node checksums verified; managed Python installed. Python 3.12.11, Node 24.13.0 and n8n 2.39.7 installed successfully; repeat `--install-only` exits successfully without service startup | PASS bootstrap; existing developer Mac is not a clean-machine acceptance |
| MAC-PW-01–10, MAC-RC-04/05, MAC-BG-02/03/13 | No physical power changes, screen locking, reboot/logout, native permission changes or long-running assertion used | NOT_RUN; require explicitly scheduled device trial |
| MAC-IN-10/11 update rollback/uninstall | No installed-user data modified | NOT_RUN |

During test-first verification, unknown launcher arguments initially reached bootstrap before rejection. The regression test exposed this; validation now occurs before creating download directories. A managed Python bootstrap initially created a uv-managed `~/.local/bin/python3.12` symlink. The trial-created symlink was removed after verifying its target belonged to this trial, and the installer now uses `uv python install --no-bin` with its cache inside the selected install root. No shell startup file or global power setting was modified.

## Completed real supervisor trials

With explicit `disable_power_assertion: true`, random private loopback ports, temporary SQLite state, managed Node 24.13.0 and real n8n 2.39.7, `tests/test_local_supervisor_integration.py` passed **4 cases in 32.97 seconds**:

- MAC-IN-07 / MAC-BG-04/08/12: zero-job app/worker/n8n readiness, repeated start reuses PID, finish-stop releases service lock, login-start preserves stopped preference.
- MAC-BG-09: active synthetic Python node finishes through real n8n before service reports stopped; new admission is rejected during drain.
- MAC-BG-10: active synthetic Python node is cancelled and run reaches `cancelled` before service reports stopped.
- MAC-RC-01/10: SIGKILL of the isolated supervisor releases child ownership; restart has a new PID/generation, fresh worker readiness and a recorded previous-seen timestamp.

The crash trial first exposed a false port-conflict from TCP TIME_WAIT. Listener preflight now uses SO_REUSEADDR plus listen validation; the same trial passed after the fix. App HTTP health must also match the lock-generation `SLEEP_IN_INSTANCE_ID`, so another HTTP 200 service cannot satisfy readiness. A separate real HTTP fixture proves mismatched instance IDs fail readiness.

Final selected automated run: `test_local_accounts.py`, `test_local_service.py` (with prepared-install opt-in) and root `test_local_integration.py`: **30 passed**, with two upstream FastAPI/Starlette deprecation warnings. The four real supervisor trials above were executed separately. `bash -n launch-mac.command` and Python compile checks passed. Native app self-test did not register or launch services.

The interrupted-install lock test failed first, then passed after stale-lock recovery was implemented. Native login permission acceptance/denial/revocation, actual caffeinate lifetime, display lock, battery transitions and daily continuity remain **NOT_RUN**, independent of these passing subprocess tests.

## Review follow-up: native quit and worker generations

The native companion now handles `applicationShouldTerminate`, routes normal menu/AppleEvent quit through the same finish/cancel choice, replies asynchronously only after service stop is verified, and stays open if stop verification fails. Its production termination policy is exercised by a side-effect-free native `--self-test`; a regression checks ask → wait → ask-after-failure → wait → exit-after-confirmed-stop. This does not claim a physical GUI/AppleEvent trial. `SleepIn-preview-local-v5.app` compiled, passed ad-hoc signature verification and returned the expected policy evidence without opening the GUI or changing registration/power state.

Worker readiness now requires the supervisor generation in addition to a fresh ready/n8n heartbeat. A real temporary-store regression proves a recent prior-generation heartbeat is rejected and the current generation is accepted. Forced process termination remains outside ordinary application-quit guarantees.
