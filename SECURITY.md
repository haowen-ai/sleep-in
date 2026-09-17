# Security

This is an early development release for trusted script authors, not a hostile-code sandbox. An administrator who publishes Python can access resources available to the worker. Operators share task histories and may see business output. Use a separate deployment for a separate trust boundary.

The default Compose configuration only exposes the web port on 127.0.0.1. Do not expose this development setup directly to the Internet. For a remote trusted team, use HTTPS at a reverse proxy, enable `COOKIE_SECURE=true`, protect network access, and review dependency updates. n8n and PostgreSQL have no published host ports.

Never put secrets in source bundles or ordinary task parameters. Variable values are encrypted at rest with a separate local key; losing the key loses access to those values. Log redaction is best effort and cannot prevent a script intentionally printing or transforming a secret. Avoid logging secrets.

For a suspected vulnerability, use the repository's GitHub private vulnerability reporting feature if available. If it is unavailable, open an issue requesting a private contact channel without including exploit details or sensitive data. Do not post working credentials or production records.

No formal penetration test or security certification has been completed.
