"""Request-scoped context and access logging.

`BaseHTTPMiddleware` is fine here: contextvars bound before `call_next` do reach
downstream handlers in this Starlette version. The one place they do not reach is the
unhandled-exception handler -- see the note in `dispatch`.
"""

from __future__ import annotations

import time
from collections.abc import Awaitable, Callable

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from insightlm.shared.domain.ids import CorrelationId
from insightlm.shared.observability.correlation import request_context
from insightlm.shared.observability.logging import get_logger

_logger = get_logger(__name__)

CORRELATION_HEADER = "X-Correlation-ID"


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Binds a correlation ID for the request and echoes it back.

    An inbound header is honoured so a trace can be followed across the frontend, the
    API and the worker.
    """

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        inbound = request.headers.get(CORRELATION_HEADER)
        started = time.perf_counter()

        with request_context(
            correlation_id=CorrelationId(inbound) if inbound else None
        ) as correlation_id:
            # Also stashed on the ASGI scope, not only in the contextvar.
            #
            # The contextvar reaches ordinary handlers fine. It does *not* reach the
            # catch-all 500 handler: Starlette runs that inside ServerErrorMiddleware,
            # which wraps every user middleware, so by then this context manager has
            # exited and the variable is unset. Unhandled errors are precisely the
            # responses where a correlation id matters most, so the scope -- which is
            # shared and outlives this block -- carries it too.
            request.scope.setdefault("state", {})["correlation_id"] = correlation_id

            response = await call_next(request)
            elapsed_ms = (time.perf_counter() - started) * 1000
            response.headers[CORRELATION_HEADER] = correlation_id

            _logger.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=round(elapsed_ms, 2),
            )
            return response
