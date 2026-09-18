"""Rate limits, spend caps and idempotency: the ports.

All three hold small, short-lived counters shared by every API process, which is what
Redis is for. Each has an in-memory adapter too, used by the tests, so the rules are
tested without a Redis in the loop.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from insightlm.shared.domain.ids import TenantId


class RateLimiter(Protocol):
    async def hit(self, key: str, *, limit: int, window_s: int) -> int:
        """Count one request. Returns 0 if allowed, else seconds until the window resets."""
        ...


class SpendMeter(Protocol):
    async def charge(self, bucket: str, units: int, *, limit: int, window_s: int) -> int:
        """Reserve ``units`` against ``bucket``. Returns the total used, or raises
        ``BudgetExceededError`` without charging if it would go over ``limit``."""
        ...


@dataclass(frozen=True, slots=True)
class StoredResponse:
    status: int
    body: bytes
    content_type: str
    fingerprint: str


class ModelBudget(Protocol):
    async def charge(self, tenant_id: TenantId, calls: int) -> None:
        """Reserve ``calls`` paid model calls for the workspace, before making them.
        Raises ``BudgetExceededError`` (and charges nothing) if that would pass the cap."""
        ...


class IdempotencyStore(Protocol):
    async def claim(self, key: str, fingerprint: str, *, ttl_s: int) -> StoredResponse | bool:
        """True if this caller now owns the key; a StoredResponse to replay; False if
        another request with the key is still in flight."""
        ...

    async def complete(self, key: str, response: StoredResponse, *, ttl_s: int) -> None: ...

    async def release(self, key: str) -> None:
        """Give up a claim without storing (the request failed; a retry may proceed)."""
        ...
