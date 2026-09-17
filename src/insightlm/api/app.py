"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from insightlm import __version__
from insightlm.api.errors import register_exception_handlers
from insightlm.api.middleware import CorrelationMiddleware
from insightlm.api.routers import ask, audit, auth, documents, health, members
from insightlm.bootstrap import Container, build_container
from insightlm.settings import Settings, get_settings
from insightlm.shared.observability.logging import configure_logging, get_logger

_logger = get_logger(__name__)

DESCRIPTION = """
Evidence-grounded document intelligence, applied to hiring.

**Phase 0** -- the platform foundation: workspaces, authentication, team invitations
with role-based access, and the tamper-evident audit log every later module writes to.

**Phase 1** -- the document engine: upload, a resumable ingestion pipeline, and
grounded question answering where *every claim carries a citation that resolves to an
exact span in the source document*. A claim whose citation cannot be resolved is never
shown.
"""


def create_app(settings: Settings | None = None, *, container: Container | None = None) -> FastAPI:
    """Build the application.

    ``container`` can be supplied by tests so they wire fakes without reaching into
    module globals.
    """
    resolved = settings or get_settings()
    configure_logging(level=resolved.log_level, json_output=resolved.log_json)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.container = container or build_container(resolved)
        # Idempotent, and required here as well as in the worker: whichever process
        # starts first creates the bucket, and either may run alone.
        await app.state.container.object_store.ensure_bucket()
        _logger.info(
            "application_started",
            environment=resolved.environment,
            version=__version__,
        )
        try:
            yield
        finally:
            if container is None:
                await app.state.container.dispose()
            _logger.info("application_stopped")

    app = FastAPI(
        title="InsightLM",
        description=DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        openapi_url="/openapi.json",
    )

    app.add_middleware(CorrelationMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(members.router)
    app.include_router(audit.router)
    app.include_router(documents.router)
    app.include_router(ask.router)

    # The bundled evidence viewer. A single page, served by the API, so the citation
    # contract can be demonstrated end to end without a Node toolchain. The production
    # frontend is a separate concern; this exists to prove the loop works.
    viewer = Path(__file__).parent / "static" / "viewer.html"

    @app.get("/", include_in_schema=False)
    async def _viewer() -> HTMLResponse:
        return HTMLResponse(viewer.read_text(encoding="utf-8"))

    return app
