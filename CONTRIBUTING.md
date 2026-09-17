# Contributing

Please open an issue describing the user problem before proposing a large feature. Small bug fixes and documentation improvements are welcome.

Use Python 3.12 and Node.js 22+ for development. Install `requirements-dev.txt`, run `python -m pytest -q`, and run the Docker Compose smoke test from a disposable, fresh deployment. Never run the smoke script against an instance with data you want to keep.

Write behavior tests for scheduling boundaries, authorization, filesystem access and subprocess lifecycle changes. Keep English and Simplified Chinese translation keys in sync. English is always the first-visit default; do not derive it from the browser language. Preserve form content during language switching.

Use synthetic examples. Do not attach API keys, passwords, real task parameters, production logs or private screenshots to issues or pull requests.

See [architecture](docs/ARCHITECTURE.md) and [script contracts](docs/SCRIPTS.md). This project deliberately targets a small trusted workspace rather than untrusted multi-tenant code execution.
