# syntax=docker/dockerfile:1

# ---- build: resolve and install the locked dependencies plus the project into a virtualenv
FROM python:3.12-slim AS builder
COPY --from=ghcr.io/astral-sh/uv:0.13.6 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---- runtime: only the virtualenv, a non-root user, JSON logs
FROM python:3.12-slim
RUN useradd --system --create-home --uid 10001 kombin
WORKDIR /app
COPY --from=builder --chown=kombin:kombin /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    KOMBIN_LOG_JSON=1
USER kombin

ENTRYPOINT ["kombin-workers"]
# run-all every 6 hours, sweep once a day (with jitter); override for one-off runs, e.g. `run koton --limit 50`
CMD ["daemon"]
