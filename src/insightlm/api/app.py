"""FastAPI application factory."""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse

from insightlm import __version__
from insightlm.api.errors import register_exception_handlers
from insightlm.api.limits import LimitsMiddleware
from insightlm.api.middleware import CorrelationMiddleware
from insightlm.api.routers import (
    ask,
    audit,
    auth,
    contracts,
    documents,
    governance,
    health,
    members,
    pipeline,
    recruiting,
)
from insightlm.api.schemas import ErrorResponse
from insightlm.bootstrap import Container, build_container
from insightlm.settings import Settings, get_settings
from insightlm.shared.observability.logging import configure_logging, get_logger
from insightlm.shared.observability.telemetry import configure_telemetry, instrument_app

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
    configure_telemetry(
        enabled=resolved.otel_enabled,
        endpoint=resolved.otel_endpoint,
        service="insightlm-api",
        environment=resolved.environment,
    )

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

    # Added first so it runs inside correlation: a 429 still carries a correlation id.
    app.add_middleware(LimitsMiddleware)
    app.add_middleware(CorrelationMiddleware)
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(members.router)
    app.include_router(audit.router)
    app.include_router(documents.router)
    app.include_router(ask.router)
    app.include_router(recruiting.router)
    app.include_router(pipeline.router)
    app.include_router(governance.router)
    app.include_router(contracts.router)
    _document_error_envelope(app)
    if resolved.otel_enabled:
        instrument_app(app)

    # The bundled evidence viewer. A single page, served by the API, so the citation
    # contract can be demonstrated end to end without a Node toolchain. The production
    # frontend is a separate concern; this exists to prove the loop works.
    viewer = Path(__file__).parent / "static" / "viewer.html"

    @app.get("/", include_in_schema=False)
    async def _viewer() -> HTMLResponse:
        return HTMLResponse(viewer.read_text(encoding="utf-8"))

    return app


_ERROR_STATUSES = {
    "401": "Not signed in, or the token is invalid",
    "403": "Signed in, but this role may not do this",
    "404": "Not found, or not visible to you",
    "409": "Conflicts with the current state",
    "422": "The request is invalid",
    "429": "Rate limited, or over the workspace's AI budget",
    "502": "A model provider returned an unusable answer",
}


def _document_error_envelope(app: FastAPI) -> None:
    """Declare the one error shape the API actually returns, on every operation.

    FastAPI documents 422 as its own `HTTPValidationError`, which this API never sends
    (validation errors go through the same envelope as every other error). A schema
    that describes responses the server doesn't produce is worse than none: generated
    clients and schemathesis both trusted it and both were wrong.
    """

    def build() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title, version=app.version, description=app.description, routes=app.routes
        )
        components = schema.setdefault("components", {}).setdefault("schemas", {})
        components["ErrorResponse"] = ErrorResponse.model_json_schema(
            ref_template="#/components/schemas/{model}"
        )
        ref = {"$ref": "#/components/schemas/ErrorResponse"}
        for path, operations in schema.get("paths", {}).items():
            if path == "/health":
                continue
            for operation in operations.values():
                responses = operation.setdefault("responses", {})
                for status, description in _ERROR_STATUSES.items():
                    responses[status] = {
                        "description": description,
                        "content": {"application/json": {"schema": ref}},
                    }
        components.pop("HTTPValidationError", None)
        components.pop("ValidationError", None)
        app.openapi_schema = schema
        return schema

    app.openapi = build  # type: ignore[method-assign]
