# syntax=docker/dockerfile:1.7
# Two targets from one file. `api` stays slim; `worker` will carry the parsers, OCR
# and reranker from Phase 1 onward. Keeping them separate means a parser CVE cannot
# reach the request path, and API deploys stay fast.

FROM python:3.12-slim-bookworm AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:$PATH"
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.5 /uv /usr/local/bin/uv


FROM base AS deps
# Dependencies resolve from the lockfile alone, so this layer is cached until the
# lockfile changes -- not on every source edit.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev


FROM base AS runtime
RUN groupadd --system app && useradd --system --gid app --home /app app
COPY --from=deps /app/.venv /app/.venv
COPY --chown=app:app src/ ./src/
COPY --chown=app:app migrations/ ./migrations/
COPY --chown=app:app alembic.ini pyproject.toml ./
USER app


FROM runtime AS api
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"
CMD ["uvicorn", "insightlm.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]


FROM runtime AS worker
CMD ["arq", "insightlm.worker.main.WorkerSettings"]
