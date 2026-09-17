# Architecture

The web service and worker use PostgreSQL as the authoritative state store. The initial schema is a JSON document table keyed by object kind and ID. A PostgreSQL transaction-scoped advisory lock serializes task revisions, deduplication and queue claims. This intentionally prioritizes simple, correct small-workspace transitions over high-volume parallel database writes. SQLite is a local development/test adapter, using immediate transactions; it is not a second production deployment profile.

n8n is the external dispatch clock. Its automatically imported and published workflow sends a signed-by-possession internal HTTP request every five seconds. The server derives the current time; callbacks cannot choose a task, alter parameters or inject code. One workflow is reused on every restart.

This is a deliberate implementation refinement from an early per-task-workflow design: all schedule previews and due dates use the same Python timezone rules, and the n8n workflow count is constant. The UI derives readiness from a recent successful callback. It never treats schedule persistence or n8n HTTP success as Python business success.

Due tasks create immutable execution documents with a deterministic submission ID. Manual requests need an Idempotency-Key. An existing queued/running/cancelling execution blocks overlapping work for that task; the new trigger is recorded as skipped. Late schedules beyond the 30-second dispatch grace are recorded as a misfire range marker, not replayed; the next future occurrence is calculated. This is not a guarantee of exactly-once external side effects.

The worker atomically claims from the persistent queue, starts a process group and renews a 45-second lease every five seconds. It marks abandoned claims interrupted instead of rerunning them. A cancellation first becomes cancelling, then a terminal state after subprocess termination. A task disable only affects future dispatches. Each run works from a copy of its published source to keep normal relative file writes from modifying version files.

Users and sessions are server-side records. Passwords use PBKDF2-SHA256 with 600,000 iterations and random salts. Session cookies are HttpOnly and SameSite Strict; authenticated writes also require a CSRF token. Resetting a password or changing account privileges revokes existing sessions. Variable ciphertext uses a local Fernet key excluded from ordinary backups.

The filesystem holds immutable version bundles, virtual environments, and per-run logs/artifacts. The application does not mount the Docker socket. Dependency installation may run package build hooks and must be treated as trusted administrator code.

## Scope and limits

One workspace and one worker service, default concurrency 2 (configurable 1–16). No distributed worker scaling, multi-tenant sandbox, or guaranteed business retry. Task execution data is retained separately from source. Use operational backups; a Git clone only recovers application code.
