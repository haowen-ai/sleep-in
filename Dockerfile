FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_STATE_DIR=/state

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
