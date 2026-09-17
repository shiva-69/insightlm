"""One exception handler, one mapping from domain error to HTTP status."""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from insightlm.api.schemas import ErrorResponse
from insightlm.shared.domain.errors import DomainError
from insightlm.shared.observability.correlation import current_correlation_id
from insightlm.shared.observability.logging import get_logger

_logger = get_logger(__name__)


def _correlation_of(request: Request) -> str | None:
    """Prefer the ASGI scope over the contextvar.

    The catch-all handler runs inside Starlette's ServerErrorMiddleware, which wraps
    every user middleware -- so the correlation contextvar has already been unwound by
    the time it executes. The scope survives, which is why the middleware writes it
    there as well.
    """
    from_scope = request.scope.get("state", {}).get("correlation_id")
    if isinstance(from_scope, str):
        return from_scope
    return current_correlation_id()


def register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(DomainError)
    async def _handle_domain_error(request: Request, exc: DomainError) -> JSONResponse:
        if exc.http_status >= 500:
            _logger.error("domain_invariant_violated", code=exc.code, message=exc.message)
        else:
            _logger.info("domain_error", code=exc.code, message=exc.message)

        return JSONResponse(
            status_code=exc.http_status,
            content=ErrorResponse(
                code=exc.code,
                message=exc.message,
                detail=exc.detail,
                correlation_id=_correlation_of(request),
            ).model_dump(mode="json"),
        )

    @app.exception_handler(Exception)
    async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        # Log the detail, return none of it: an unexpected error's message may contain
        # anything, including data the caller must not see.
        _logger.exception("unhandled_error", error_type=type(exc).__name__)
        return JSONResponse(
            status_code=500,
            content=ErrorResponse(
                code="internal_error",
                message="an unexpected error occurred",
                correlation_id=_correlation_of(request),
            ).model_dump(mode="json"),
        )
