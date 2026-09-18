FROM node:24.13.0-bookworm-slim AS node-runtime
RUN npm install --prefix /opt/n8n --no-audit --no-fund n8n@2.39.7

FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_STATE_DIR=/state

COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY --from=node-runtime /opt/n8n /opt/n8n
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && apt-get update && apt-get install -y --no-install-recommends ca-certificates procps gcc g++ openjdk-17-jdk-headless \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 taskconsole \
    && useradd --uid 1000 --gid 1000 --create-home taskconsole

WORKDIR /app
COPY requirements.txt pyproject.toml ./
COPY taskconsole ./taskconsole
COPY examples ./examples
RUN pip install --no-cache-dir .

USER 1000:1000
EXPOSE 8080
CMD ["python", "-m", "taskconsole", "serve"]
