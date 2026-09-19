"""Redis and in-memory adapters for the limit ports."""

from __future__ import annotations

import json
import time
from typing import Any

from insightlm.shared.domain.errors import BudgetExceededError, ServiceUnavailableError
from insightlm.shared.domain.ids import TenantId
from insightlm.shared.ports.limits import SpendMeter, StoredResponse

_PENDING = "__pending__"

# Charge atomically, refusing (and not charging) if the total would pass the limit.
_CHARGE = """
local used = tonumber(redis.call('GET', KEYS[1]) or '0')
local units = tonumber(ARGV[1])
if used + units > tonumber(ARGV[2]) then return -1 end
used = redis.call('INCRBY', KEYS[1], units)
if used == units then redis.call('EXPIRE', KEYS[1], ARGV[3]) end
return used
"""


class RedisRateLimiter:
    """Fixed window. Coarser than a sliding window, and one INCR per request."""

    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def hit(self, key: str, *, limit: int, window_s: int) -> int:
        window = int(time.time() // window_s)
        name = f"rl:{key}:{window}"
        count = await self._redis.incr(name)
        if count == 1:
            await self._redis.expire(name, window_s)
        if count <= limit:
            return 0
        return max(1, window_s - int(time.time()) % window_s)


class RedisSpendMeter:
    def __init__(self, redis: Any) -> None:
        self._redis = redis
        self._script = redis.register_script(_CHARGE)

    async def charge(self, bucket: str, units: int, *, limit: int, window_s: int) -> int:
        window = int(time.time() // window_s)
        used = int(
            await self._script(keys=[f"spend:{bucket}:{window}"], args=[units, limit, window_s])
        )
        if used < 0:
            raise BudgetExceededError(
                "this workspace has used its AI budget for today",
                detail={"limit": limit, "resets_in_s": window_s - int(time.time()) % window_s},
            )
        return used


class RedisIdempotencyStore:
    def __init__(self, redis: Any) -> None:
        self._redis = redis

    async def claim(self, key: str, fingerprint: str, *, ttl_s: int) -> StoredResponse | bool:
        name = f"idem:{key}"
        if await self._redis.set(name, _PENDING, nx=True, ex=ttl_s):
            return True
        raw = await self._redis.get(name)
        if raw is None or raw in (_PENDING, _PENDING.encode()):
            return False
        data = json.loads(raw)
        return StoredResponse(
            status=data["status"],
            body=data["body"].encode("latin-1"),
            content_type=data["content_type"],
            fingerprint=data["fingerprint"],
        )

    async def complete(self, key: str, response: StoredResponse, *, ttl_s: int) -> None:
        await self._redis.set(
            f"idem:{key}",
            json.dumps(
                {
                    "status": response.status,
                    "body": response.body.decode("latin-1"),
                    "content_type": response.content_type,
                    "fingerprint": response.fingerprint,
                }
            ),
            ex=ttl_s,
        )

    async def release(self, key: str) -> None:
        await self._redis.delete(f"idem:{key}")


class MemoryRateLimiter:
    def __init__(self) -> None:
        self._counts: dict[str, int] = {}

    async def hit(self, key: str, *, limit: int, window_s: int) -> int:
        name = f"{key}:{int(time.time() // window_s)}"
        self._counts[name] = self._counts.get(name, 0) + 1
        return 0 if self._counts[name] <= limit else max(1, window_s - int(time.time()) % window_s)


class MemorySpendMeter:
    def __init__(self) -> None:
        self.used: dict[str, int] = {}

    async def charge(self, bucket: str, units: int, *, limit: int, window_s: int) -> int:
        name = f"{bucket}:{int(time.time() // window_s)}"
        if self.used.get(name, 0) + units > limit:
            raise BudgetExceededError(
                "this workspace has used its AI budget for today", detail={"limit": limit}
            )
        self.used[name] = self.used.get(name, 0) + units
        return self.used[name]


class MemoryIdempotencyStore:
    def __init__(self) -> None:
        self._items: dict[str, StoredResponse | str] = {}

    async def claim(self, key: str, fingerprint: str, *, ttl_s: int) -> StoredResponse | bool:
        existing = self._items.get(key)
        if existing is None:
            self._items[key] = _PENDING
            return True
        return False if existing == _PENDING else existing  # type: ignore[return-value]

    async def complete(self, key: str, response: StoredResponse, *, ttl_s: int) -> None:
        self._items[key] = response

    async def release(self, key: str) -> None:
        self._items.pop(key, None)


class MeteredBudget:
    """A daily cap on paid model calls per workspace."""

    def __init__(self, meter: SpendMeter, *, calls_per_day: int) -> None:
        self._meter = meter
        self._limit = calls_per_day

    async def charge(self, tenant_id: TenantId, calls: int) -> None:
        if calls <= 0:
            return
        try:
            await self._meter.charge(
                f"model:{tenant_id}", calls, limit=self._limit, window_s=86_400
            )
        except BudgetExceededError:
            raise
        except Exception as error:
            # Fail closed, unlike rate limiting: without the meter, paid calls would run
            # unmetered. Model features pause; everything else keeps working.
            raise ServiceUnavailableError(
                "AI features are briefly unavailable; please retry shortly"
            ) from error


class UnmeteredBudget:
    """For the offline adapters: nothing is spent, so nothing is counted."""

    async def charge(self, tenant_id: TenantId, calls: int) -> None:
        return None
