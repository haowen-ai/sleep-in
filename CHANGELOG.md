# Changelog

Sleep In follows [Semantic Versioning](https://semver.org/). Every published version has a matching Git tag and GitHub Release. Pre-release versions remain development previews until the release gates in `docs/VERIFICATION.md` are complete.

## [Unreleased]

Changes being prepared for the next version belong here.

## [0.2.0-beta.1] - 2026-09-18

### Added

- Visual workflow editing with a fixed downward flowchart, branching, joins, node insertion, replacement, copying, deletion and endpoint reassignment.
- SQL receiver nodes for SQLite, PostgreSQL, MySQL and Oracle, plus Python, JavaScript, Shell, Java, C and C++ script nodes.
- Explicit typed mappings so a node can consume selected upstream outputs, constants, variables or no upstream output at all.
- Plain-language manual, interval, daily, weekday, weekly and monthly schedules with timezone-aware previews; users do not need to write cron expressions.
- Execution history, streamed redacted logs, artifacts, cancellation, retries, timeouts, notifications, backups and recovery controls.
- English-first interface with a complete Simplified Chinese switch, including the sign-in and health surfaces.
- Managed Mac runtime bootstrap, background supervisor, idle-sleep prevention status and native start, stop and recovery controls.

### Changed

- Renamed the product to Sleep In / 不再早起 and focused the experience on reusable multi-step scheduled workflows instead of one script per timer.
- Replaced free-position node dragging with dependency-driven downward layout so editing behaves like a readable flowchart.
- Separated coordinator readiness from n8n engine health and made degraded states visible.

### Fixed

- Repaired managed Python packs whose copied interpreter could lose its dynamic-library path on Apple silicon.
- Settled late cancellation correctly when an external SQL call returned after cancellation was requested.
- Redacted secrets before log chunks are written to disk, including values split across streaming boundaries.
- Prevented the native Mac controller from blocking while commands emit large output.
- Completed missing locale updates and browser pause-one/pause-all behavior.
- Removed a timing race from browser acceptance checks by waiting for asynchronous run-detail rendering under delayed API responses.

### Known limitations

- This is a beta release. The Mac application is ad-hoc signed and has not been Developer ID signed or notarized.
- Physical clean-Mac installation, lock-screen, battery, login/reboot and multi-day power trials remain release gates.
- A Mac must remain powered on and awake at the operating-system level. Shutdown, exhausted battery, lid-close sleep and explicit system sleep prevent execution.

## [0.1.0] - 2026-09-17

### Added

- English-first task console with explicit Simplified Chinese switching.
- Versioned Python tasks, seven scheduling modes and n8n dispatch integration.
- Execution queue, logs, cancellation, timeouts and downloadable artifacts.
- Local setup, roles, variables and backup/restore commands.

[Unreleased]: https://github.com/haowenchen0811/sleep-in/compare/v0.2.0-beta.1...HEAD
[0.2.0-beta.1]: https://github.com/haowenchen0811/sleep-in/releases/tag/v0.2.0-beta.1
[0.1.0]: https://github.com/haowenchen0811/sleep-in/releases/tag/v0.1.0
