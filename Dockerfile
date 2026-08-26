FROM python:3.14-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.12.5 /uv /uvx /bin/

ENV UV_PYTHON_DOWNLOADS=0 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Dependencies resolve from the lockfile alone, so this layer is cached until
# pyproject.toml or uv.lock actually change.
#
# `--all-extras`, matching CI: one image serves ingest, the analyser and the API, and the
# `api` service's command is `uvicorn`. Installing only the ingest extra left that binary
# out of the image, so the service in the `full` profile could not start at all.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-editable --all-extras

COPY . /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-editable --all-extras

FROM python:3.14-slim

# Run as a non-root user: this process parses HTML from untrusted sources.
RUN useradd --create-home --uid 10001 app

COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app /app

WORKDIR /app
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONPATH="/app/src" \
    PYTHONUNBUFFERED=1

USER app
