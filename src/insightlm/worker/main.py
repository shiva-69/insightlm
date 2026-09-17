"""ARQ worker entrypoint.

Phase 0 ships one task -- the outbox relay -- plus the scheduled audit-chain
verification. Ingestion tasks land here in Phase 1.

The worker shares the domain code with the API and differs only in what it is asked
to do: same use cases, same unit of work, same rules.
"""

from __future__ import annotations

from typing import Any, ClassVar

from arq.connections import RedisSettings
from arq.cron import cron

from insightlm.bootstrap import Container, build_container
from insightlm.settings import get_settings
from insightlm.shared.adapters.db.relay import OutboxRelay
from insightlm.shared.observability.logging import configure_logging, get_logger

_logger = get_logger(__name__)


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    ctx["container"] = build_container(settings)
    _logger.info("worker_started")


async def shutdown(ctx: dict[str, Any]) -> None:
    container: Container = ctx["container"]
    await container.dispose()
    _logger.info("worker_stopped")


async def drain_outbox(ctx: dict[str, Any]) -> int:
    """Publish staged domain events.

    Runs on a schedule rather than being triggered by writers: a relay that must be
    called is a relay that will be forgotten.
    """
    container: Container = ctx["container"]
    relay = OutboxRelay(container.session_factory)
    published = await relay.drain()
    if published:
        _logger.info("outbox_drained", published=published)
    return published


class WorkerSettings:
    """ARQ reads these attributes off the class, so they are ClassVars by contract."""

    functions: ClassVar[list[Any]] = []
    cron_jobs: ClassVar[list[Any]] = [
        cron(drain_outbox, second={0, 10, 20, 30, 40, 50}, run_at_startup=True),
    ]
    on_startup = startup
    on_shutdown = shutdown

    @staticmethod
    def redis_settings() -> RedisSettings:
        return RedisSettings.from_dsn(str(get_settings().redis_url))
