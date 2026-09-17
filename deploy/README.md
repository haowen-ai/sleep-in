# Deployment notes

`docker compose up --build --wait` is the supported single-host deployment. The
browser application listens only on `127.0.0.1:8080`; PostgreSQL and n8n are
available only on the private Compose network.

## Environment

- `POSTGRES_PASSWORD` changes the private database password. Set it before the
  first startup for any long-lived installation.
- `TASK_CONSOLE_IMAGE_TAG` optionally selects the local application image tag.
- The application receives `APP_STATE_DIR=/state` and `DATABASE_URL` from
  Compose. Do not expose either as a task-process environment variable.

The first boot initializes the database and state volume. The application
creates `/state/dispatch-token` with mode `0600`, and the initializer copies
only that file into a dedicated volume. All application and n8n containers use
UID/GID 1000. n8n mounts the dedicated volume read-only at `/state`; it cannot
read setup credentials, variable encryption keys, task bundles, or outputs.

## n8n bootstrap

The pinned n8n image uses the n8n server CLI to unpublish any existing fixed-ID
workflow, import `deploy/n8n/heartbeat.json`, and publish it before the n8n
server starts. A restart updates the same workflow instead of creating another
one. This path needs no n8n account, editor interaction, public API key, or
direct n8n database writes.

The workflow runs every five seconds, reads the dispatch token from the
read-only state mount, and sends it as `X-Dispatch-Token` to
`http://web:8080/internal/tick`. Task Console remains responsible for task
timezone and DST calculations; n8n supplies only the heartbeat.

The bootstrap uses the supported n8n 2.x commands:

```text
n8n unpublish:workflow --id=task-console-heartbeat
n8n import:workflow --input=/bootstrap/heartbeat.json
n8n publish:workflow --id=task-console-heartbeat
```

The Compose smoke test waits for `scheduler.last_tick`, reruns the initializer,
and checks that exactly one heartbeat workflow remains. Docker is unavailable
in the repository's authoring environment, so container execution is verified
by the CI Compose job rather than claimed from local checks.
