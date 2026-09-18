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


FROM base AS build
# Dependencies resolve from the lockfile alone, so this layer stays cached until the
# lockfile changes -- not on every source edit.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

# Then the project itself. Installing it -- rather than leaning on PYTHONPATH -- is
# what makes `insightlm` importable the same way it is in development, and it is the
# step whose absence made every container exit with ModuleNotFoundError.
COPY src/ ./src/
COPY migrations/ ./migrations/
COPY alembic.ini ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev


FROM base AS runtime
RUN groupadd --system app && useradd --system --gid app --home /app app
COPY --from=build --chown=app:app /app/.venv /app/.venv
COPY --chown=app:app src/ ./src/
COPY --chown=app:app migrations/ ./migrations/
COPY --chown=app:app alembic.ini pyproject.toml ./
USER app
# Fail fast and loudly if the package did not make it into the image, rather than
# discovering it from a stack trace at container start.
RUN python -c "import insightlm; print('insightlm', insightlm.__version__)"


FROM runtime AS api
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://localhost:8000/health').status==200 else 1)"
CMD ["uvicorn", "insightlm.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]


FROM runtime AS worker
CMD ["arq", "insightlm.worker.main.WorkerSettings"]
