"""Rate limits and idempotency keys, at the HTTP edge.

**Rate limits** are per principal and per class of endpoint. Three classes, because
three different things are being protected:

- *credentials* (login, register, accepting an invitation): per client IP, tight,
  because the attack is guessing passwords and it comes before anyone is identified;
- *model* (ask, assess, talent search): per workspace, because each call spends money
  and one workspace's burst must not become everyone's latency;
- *everything else*: per user, generous, to stop runaway clients and scripts.

**Idempotency keys** make a retried write safe. A client that sends `Idempotency-Key`
on a POST/PUT/PATCH/DELETE and retries after a timeout gets the original response
replayed rather than a second candidate, upload or assessment. Same semantics as
Stripe's: a key reused with a different body is refused; a duplicate arriving while
the first is still running gets 409; failures (5xx) aren't stored, so a retry may run.

The client IP comes from `X-InsightLM-Client-IP` only when the API is configured to
sit behind the web proxy (which sets it and strips any inbound copy). Otherwise it's
the socket address. A header anyone can send is never trusted.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from insightlm.shared.observability.logging import get_logger
from insightlm.shared.ports.limits import IdempotencyStore, RateLimiter, StoredResponse

_logger = get_logger(__name__)

CLIENT_IP_HEADER = "x-insightlm-client-ip"
IDEMPOTENCY_HEADER = "idempotency-key"
IDEMPOTENCY_TTL_S = 24 * 3600
_MUTATING = frozenset({"POST", "PUT", "PATCH", "DELETE"})


@dataclass(frozen=True, slots=True)
class Rule:
    name: str
    limit: int
    window_s: int
    by: str  # "ip" | "tenant" | "user"


CREDENTIALS = Rule("credentials", limit=10, window_s=60, by="ip")
MODEL = Rule("model", limit=30, window_s=60, by="tenant")
DEFAULT = Rule("default", limit=300, window_s=60, by="user")

_CREDENTIAL_PATHS = re.compile(r"^/auth/(login|register|invitations/accept)$")
_MODEL_PATHS = re.compile(r"^/(ask|applications/[^/]+/assessments|talent/search)$")


def _identify(container: object, token: str) -> tuple[str, str] | None:
    try:
        claims = container.token_issuer.verify_access_token(token)  # type: ignore[attr-defined]
    except Exception:
        return None
    return str(claims.tenant_id), str(claims.user_id)


def classify(method: str, path: str) -> Rule:
    if method == "POST" and _CREDENTIAL_PATHS.match(path):
        return CREDENTIALS
    if _MODEL_PATHS.match(path):
        return MODEL
    return DEFAULT


def _error(status: int, code: str, message: str, request: Request, **headers: str) -> JSONResponse:
    correlation = request.scope.get("state", {}).get("correlation_id")
    return JSONResponse(
        status_code=status,
        content={"code": code, "message": message, "detail": {}, "correlation_id": correlation},
        headers=headers,
    )


class LimitsMiddleware(BaseHTTPMiddleware):
    """Collaborators come from the app's container at request time: middleware is
    registered before startup, and the container only exists after it."""

    def _client_ip(self, request: Request, trust_proxy_ip: bool) -> str:
        if trust_proxy_ip and (forwarded := request.headers.get(CLIENT_IP_HEADER)):
            return forwarded.strip()
        return request.client.host if request.client else "unknown"

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        container = request.app.state.container
        if (
            request.url.path in ("/health", "/metrics")
            or not container.settings.rate_limits_enabled
        ):
            return await call_next(request)
        limiter: RateLimiter = container.rate_limiter
        idempotency: IdempotencyStore = container.idempotency

        auth = request.headers.get("authorization", "")
        principal = _identify(container, auth[7:]) if auth.lower().startswith("bearer ") else None
        ip = self._client_ip(request, container.settings.trust_proxy_client_ip)

        rule = classify(request.method, request.url.path)
        subject = {
            "ip": ip,
            "tenant": principal[0] if principal else ip,
            "user": principal[1] if principal else ip,
        }[rule.by]
        try:
            wait = await limiter.hit(
                f"{rule.name}:{subject}", limit=rule.limit, window_s=rule.window_s
            )
        except Exception as error:
            # Fail open: a Redis outage must not take the whole API down with it.
            # Losing throttling for a few minutes is the smaller harm; it's logged.
            _logger.warning("rate_limiter_unavailable", error=type(error).__name__)
            wait = 0
        if wait:
            return _error(
                429,
                "rate_limited",
                "too many requests; slow down and retry",
                request,
                **{"Retry-After": str(wait)},
            )

        key = request.headers.get(IDEMPOTENCY_HEADER)
        if request.method not in _MUTATING or not key:
            return await call_next(request)
        if len(key) > 255:
            return _error(422, "validation_error", "Idempotency-Key is too long", request)

        owner = principal[1] if principal else ip
        scoped = f"{owner}:{request.method}:{request.url.path}:{key}"
        body = await request.body()
        fingerprint = hashlib.sha256(body).hexdigest()

        try:
            claimed = await idempotency.claim(scoped, fingerprint, ttl_s=IDEMPOTENCY_TTL_S)
        except Exception as error:
            # Fail open, as above: the write proceeds without replay protection.
            _logger.warning("idempotency_store_unavailable", error=type(error).__name__)
            return await call_next(request)
        if claimed is False:
            return _error(
                409, "conflict", "a request with this Idempotency-Key is in progress", request
            )
        if isinstance(claimed, StoredResponse):
            if claimed.fingerprint != fingerprint:
                return _error(
                    422,
                    "idempotency_mismatch",
                    "this Idempotency-Key was used with a different request body",
                    request,
                )
            return Response(
                content=claimed.body,
                status_code=claimed.status,
                media_type=claimed.content_type,
                headers={"Idempotent-Replayed": "true"},
            )

        try:
            response = await call_next(request)
        except Exception:
            await idempotency.release(scoped)
            raise
        if response.status_code >= 500:
            await idempotency.release(scoped)
            return response

        chunks = [chunk async for chunk in response.body_iterator]  # type: ignore[attr-defined]
        payload = b"".join(c if isinstance(c, bytes) else c.encode() for c in chunks)
        await idempotency.complete(
            scoped,
            StoredResponse(
                status=response.status_code,
                body=payload,
                content_type=response.headers.get("content-type", "application/json"),
                fingerprint=fingerprint,
            ),
            ttl_s=IDEMPOTENCY_TTL_S,
        )
        return Response(
            content=payload,
            status_code=response.status_code,
            headers=dict(response.headers),
        )
