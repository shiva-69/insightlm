"""Correlation IDs carried on a context var.

Bound once per request or task; every log line in that scope picks it up
automatically, so no function has to thread it through its signature.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from uuid import uuid4

from insightlm.shared.domain.ids import CorrelationId, TenantId, UserId

_correlation_id: ContextVar[CorrelationId | None] = ContextVar("correlation_id", default=None)
_tenant_id: ContextVar[TenantId | None] = ContextVar("tenant_id", default=None)
_user_id: ContextVar[UserId | None] = ContextVar("user_id", default=None)


def new_correlation_id() -> CorrelationId:
    return CorrelationId(uuid4().hex)


def current_correlation_id() -> CorrelationId | None:
    return _correlation_id.get()


def current_tenant_id() -> TenantId | None:
    return _tenant_id.get()


def current_user_id() -> UserId | None:
    return _user_id.get()


@contextmanager
def request_context(
    *,
    correlation_id: CorrelationId | None = None,
    tenant_id: TenantId | None = None,
    user_id: UserId | None = None,
) -> Iterator[CorrelationId]:
    """Bind request-scoped identity, restoring the previous values on exit."""
    cid = correlation_id or new_correlation_id()
    tokens = (
        _correlation_id.set(cid),
        _tenant_id.set(tenant_id),
        _user_id.set(user_id),
    )
    try:
        yield cid
    finally:
        _correlation_id.reset(tokens[0])
        _tenant_id.reset(tokens[1])
        _user_id.reset(tokens[2])
