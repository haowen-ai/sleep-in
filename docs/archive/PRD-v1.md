# Sleep In · 不再早起 — Product requirements

## Purpose

A self-hosted console lets trusted users schedule Python scripts through simple forms. n8n drives dispatch in the background. The repository is independently authored and distributes no private predecessor code, business configuration or data.

## Language

English is the initial language regardless of browser locale. English README is the repository entrypoint; a linked Simplified Chinese README provides setup guidance. The UI offers an explicit Chinese switch on setup, login and authenticated pages. Remember browser and account preference. Switching must retain unsaved form values and never change a task timezone, schedule, source or user data.

## Core journeys

1. Clone, start Docker Compose, retrieve a one-time local token, create the owner, select timezone.
2. Select a safe standard-library example, preview a one-minute schedule, enable it, inspect an actual run and download a generated file.
3. Publish a Python file or ZIP entrypoint with parameters and pinned dependencies; tasks select an immutable published version.
4. Manage tasks and inspect a shared workspace as an operator; reserve source publication, variables and account administration for administrators.
5. Stop scheduling, back up without credential material, restore to a fresh deployment with schedules disabled, re-enter variables, review and selectively enable.

## Scheduling and execution

Seven modes: manual, elapsed-minute interval, daily, Monday–Friday, selected weekdays, monthly day, standard five-field cron. IANA timezones. Missing monthly days and DST gaps skip; folds choose the first wall time. Preview and dispatch share a calculation. Late occurrences are skipped, not replayed in a burst. A restarted worker does not rerun a previously claimed business execution.

Parameters are string values, up to 100 keys, 1 MiB per value and 5 MiB combined. Recipients are a distinct optional array. Secrets use scoped environment variables. One task has at most one queued/running/cancelling execution; global concurrency defaults to two. Manual API submissions are idempotent. Timeout defaults to three hours. Cancellation terminates the process group before reporting the final state.

## Script and output contract

A single-file synchronous main(params) is called once; otherwise normal top-level code runs once. ZIP main.py executes once as a script. Static inspection does not import code. Dependency builds can execute third-party build hooks. Published versions and run snapshots are immutable to ordinary edits. Failed versions cannot be selected; active references protect retirement and archival.

Expose TASK_PARAMS_FILE, TASK_OUTPUT_DIR and TASK_RUN_ID. Log cap 20 MiB; artifact cap 100 MiB and 100 files. Reject path escapes and symlinks. Output collection failure is independent of Python success. Retain logs/outputs 30 days and metadata 90 days by default.

## Accounts and operations

No default admin password. Passwords at least 12 characters, login throttling, server-side roles, CSRF protection, revocable sessions, last-admin protection and local administrator recovery. Variable values are encrypted and never returned through management APIs. Scripts are trusted; process groups and virtual environments are not a hostile-code sandbox.

Default host access is loopback. Keep the n8n editor and database private. Health views distinguish database availability, scheduler heartbeat and worker heartbeat. Do not claim a queued or n8n-accepted job has succeeded.

## Release evidence

Unit/API/runtime tests, real n8n initialization, actual browser checks, PostgreSQL transactions and a clean Compose scheduled execution are separate gates. Publish measured results in VERIFICATION.md. Multiarch images, Windows/macOS Docker validation, onboarding studies and performance targets must not be implied by passing unit tests.

## Later scope

Advanced shared-environment editing, built-in email notifications, sanitized template import/export. No SaaS tenancy, billing, arbitrary-language runner, DAG editor, automatic business retries or migration of private predecessor data.

## Product identity and presentation

The public product is Sleep In (不再早起 in Simplified Chinese). English is the initial language; users can explicitly switch to Chinese without losing entered form values. Both READMEs include the user journey, architecture flow, setup, script authoring and operations. The console uses a light sidebar, green accent, compact tables and consistent Lucide icons. Internal `taskconsole` commands and the Compose project identifier remain stable so existing installations retain their storage.
