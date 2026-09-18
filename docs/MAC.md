# Local Mac preview

The local preview runs Sleep In on loopback with managed Python, Node and n8n. It uses the native n8n CLI for each admitted graph, not a separate n8n web account. Workflow scripts run as the signed-in user; this is a trusted workspace, not a hostile-code sandbox.

## Launch and install

Double-click `launch-mac.command` from the source distribution, or use the built **Sleep In.app** companion. The source launcher opens Terminal; the app wrapper performs the same installation in the background and opens the browser after component readiness. This preview targets Apple silicon/macOS 13+. Intel is explicitly rejected until its package is separately tested.

The installer downloads uv 0.8.22 and Node 24.13.0 archives and verifies embedded SHA-256 values before extraction/execution. uv installs Python 3.12.11 with `--no-bin`; it does not modify shell startup files or install a global Python executable. Python requirements are pinned at the application's direct dependency level. npm installs n8n 2.39.7 and uses registry package integrity checks. Transitive dependency locks and offline redistribution remain release work; this is a downloaded runtime bootstrap, not a claim that runtimes are bundled in the app.

Private state and runtimes default to `~/Library/Application Support/Sleep In`. `SLEEP_IN_INSTALL_DIR` allows an isolated installation for testing. The app listens at `http://127.0.0.1:8765`; its configuration is `state/local-config.json`. A conflicting port fails before launching services. The supervisor checks application HTTP readiness, a fresh workflow-worker heartbeat and n8n CLI availability. It opens the browser only after these pass.

On a fresh local installation, the login card shows **admin / sleepin123456**. Change it in Account → Change password. The card disappears after changing/resetting the initial hash. Existing users and populated/restored installations are never reset. Root application boundaries refuse external/public-default use and forwarded local-bootstrap requests.

## Background operation

The native menu-bar companion starts the background service on initial launch. It offers **Start at Login** explicitly using `SMAppService.mainApp`, shows pending approval and opens System Settings when macOS requires the owner's action. Registration is never performed by build scripts, tests or the command-line launcher. No Accessibility or Full Disk Access permission is requested.

While running, `/usr/bin/caffeinate -i -w <supervisor-pid>` requests idle-system-sleep prevention on **both AC and battery**. It permits display sleep/locking. Zero workflows, paused triggers, finishing a run and closing browser/app windows do not end the service. Idle-sleep prevention consumes battery and does not override lid closure, explicit system Sleep, logout, shutdown, thermal or critical-battery decisions.

**Stop background service** distinguishes finishing active work from cancelling it. Admission stops immediately via a persisted marker; status remains **draining** until runs have terminal states and owned processes exit. **Quit completely** waits for that completed stop before closing the companion. The same asynchronous drain/cancel path handles ordinary macOS application-quit events, so a standard Quit cannot bypass service shutdown. Force Quit/SIGKILL cannot run that delegate and may leave the detached supervisor running; use the explicit Stop action, or reopen the companion and stop it. An intentional stopped preference survives login/restart until **Start background service** is selected. The native companion observes permission loss and requests a stop rather than silently re-registering.

A lock with a generation token prevents duplicate coordinators and stale ready status. Child wrappers monitor a pipe from the supervisor and terminate their owned process groups after parent loss. The native companion retries an unexpectedly stopped supervisor with bounded backoff (five automatic attempts); it never overrides the stopped preference. An unexpected crash can create a gap. Status records recovery timestamps rather than asserting uninterrupted operation.

Developer commands, using the installed managed interpreter and source directory:

```sh
python -m taskconsole.local --state /absolute/private/state status
python -m taskconsole.local --state /absolute/private/state start --explicit --open
python -m taskconsole.local --state /absolute/private/state stop --mode finish
python -m taskconsole.local --state /absolute/private/state stop --mode cancel
```

`launch-mac.command --install-only` prepares runtimes without starting services. `--status`, `--stop-finish`, `--stop-cancel`, `--login` and `--start` support the native wrapper. `--login` respects a persisted stopped preference; `--start` explicitly resumes.

## Build and verify

A build machine with Swift command-line tools can run:

```sh
python packaging/build_mac.py /absolute/new/path/Sleep\ In.app
```

The builder refuses to overwrite an existing app, copies only application resources and compiles an Apple silicon native companion. It applies an **ad-hoc signature**, which is not Developer ID signing or notarization. The executable's `--self-test` verifies packaged resources without opening the UI, registering a login item, starting services or holding a power assertion.

Automated isolated supervisor tests use `disable_power_assertion: true` in their private local configuration. That is a test-only deployment choice, explicitly reported as `disabled-for-test`; it cannot establish real power behavior. Never disable assertions in a release configuration to make a failed power test look successful.

See [local test design](testing/local-lifecycle-cases.md) and [results](testing/local-results.md). Clean-Mac installation, native registration accepted/denied/revoked paths, signed distribution, physical AC/battery/lock trials, daily soak, update rollback, recovery across login/reboot and battery alerts require their own recorded evidence. A successful build or short subprocess test does not close those release gates.
