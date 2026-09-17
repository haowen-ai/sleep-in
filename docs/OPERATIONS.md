# Operations

## Local development

`APP_STATE_DIR` selects the state directory. `DATABASE_URL` selects PostgreSQL (`postgresql+psycopg://...`) or a disposable SQLite test database. The default local directory is `state/`, ignored by Git. Run `python -m taskconsole init`, then `serve` and `worker` in separate terminals. Manual tasks work without n8n; automatic dispatch requires Compose's n8n service.

## Account recovery

From the deployment host:

```bash
docker compose exec web python -m taskconsole reset-password YOUR_USERNAME
```

The command asks for a new password privately, restores administrator access to that local account and revokes its sessions. There is no default password. Do not pass the new password on the command line.

## Backup

Stop scheduling and finish/cancel active jobs before backup. Keep backups private: source, parameter values and outputs may contain business information even though authentication secrets are excluded.

```bash
docker compose stop n8n worker
docker compose exec web python -m taskconsole backup /state/backup.zip
mkdir -p backups
docker compose cp web:/state/backup.zip ./backups/backup.zip
docker compose start worker n8n
```

The backup includes application metadata and versioned scripts. Add `--include-runs` to include retained logs and output files. Each backup destination must be new. Ordinary backups exclude users/password hashes, sessions, variable values, the variable encryption key, setup token, dispatch token, and n8n's own private configuration. Store variables in your password manager or a separate protected backup. Do not upload operational backups to GitHub.

## Restore to a separate fresh deployment

Restore requires a fresh application database with no accounts or script versions. Do not run app-init before restoring. Start only postgres and state-permissions, then run the application image with the target database/state mounted and `python -m taskconsole restore /path/to/backup.zip`. The application image must be built first with `docker compose build app-init`.

For a repository checkout with the backup at `./backups/backup.zip`:

```bash
docker compose build app-init
docker compose up -d postgres
docker compose run --rm state-permissions
docker compose run --rm --no-deps -v "$PWD/backups:/backups:ro" app-init python -m taskconsole restore /backups/backup.zip
docker compose up -d
```

Use a separate Compose project name (`COMPOSE_PROJECT_NAME`) and an unused web port for a restore rehearsal. All restored schedules are disabled, users are recreated through setup, and variables must be re-entered. Dependency environments are rebuilt from recorded pins; a deleted upstream package may need manual recovery. Review data and dependencies, then enable individual tasks. Never enable the original and restored instances simultaneously for the same business job.

## Updates and remote access

Back up first, review release notes, then `git pull` and `docker compose up -d --build`. Fixed images prevent surprise dependency upgrades. Never use volume deletion as a routine upgrade step.

For remote team use, put an HTTPS reverse proxy in front of web, set COOKIE_SECURE=true, and explicitly adjust the host bind. Do not publish n8n or PostgreSQL ports. Consult the n8n dependency license for your use case. Review the internal database credential configuration before expanding network access.
