"""ARQ worker entrypoint.

Same codebase as the API, different entrypoint: same domain, same unit of work, same
rules. What differs is only what it is asked to do.
"""

from __future__ import annotations

from typing import Any, ClassVar
from uuid import UUID

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.cron import cron

from insightlm.bootstrap import Container, build_container
from insightlm.settings import get_settings
from insightlm.shared.adapters.db.relay import OutboxRelay
from insightlm.shared.domain.ids import DocumentVersionId, TenantId
from insightlm.shared.observability.logging import configure_logging, get_logger

_logger = get_logger(__name__)

INGEST_TASK = "ingest_document_version"

_EVENT_TASKS = {"document.ingest_requested": INGEST_TASK}


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    ctx["container"] = build_container(settings)
    await ctx["container"].object_store.ensure_bucket()
    _logger.info("worker_started")


async def shutdown(ctx: dict[str, Any]) -> None:
    container: Container = ctx["container"]
    await container.dispose()
    _logger.info("worker_stopped")


async def ingest_document_version(
    ctx: dict[str, Any], tenant_id: str, document_version_id: str
) -> dict[str, Any]:
    """Run the ingestion pipeline for one document version.

    Idempotent: the pipeline resumes from the job's recorded steps, so a redelivered
    message re-runs at most the step that was in flight.
    """
    container: Container = ctx["container"]
    outcome = await container.ingestion.run(
        tenant_id=TenantId(UUID(tenant_id)),
        version_id=DocumentVersionId(UUID(document_version_id)),
    )
    return {
        "completed": outcome.completed,
        "steps_run": [step.value for step in outcome.steps_run],
        "dead_lettered": outcome.dead_lettered,
        "error": outcome.error,
    }


async def drain_outbox(ctx: dict[str, Any]) -> int:
    """Publish staged domain events onto the queue.

    Scheduled rather than triggered by writers: a relay that must be called is a relay
    that will eventually be forgotten.
    """
    container: Container = ctx["container"]
    redis: ArqRedis = ctx["redis"]

    async def publish(event_type: str, payload: dict[str, Any], tenant_id: str) -> None:
        task = _EVENT_TASKS.get(event_type)
        if task is None:
            return  # nothing consumes this event type yet
        await redis.enqueue_job(task, tenant_id, payload["document_version_id"])

    published = await OutboxRelay(container.session_factory, publisher=publish).drain()
    if published:
        _logger.info("outbox_drained", published=published)
    return published


class WorkerSettings:
    """ARQ reads these attributes off the class, so they are ClassVars by contract."""

    functions: ClassVar[list[Any]] = [ingest_document_version]
    cron_jobs: ClassVar[list[Any]] = [
        cron(drain_outbox, second={0, 10, 20, 30, 40, 50}, run_at_startup=True),
    ]
    on_startup = startup
    on_shutdown = shutdown
    max_tries = 1
    """Retries are the pipeline's job, not the queue's: `IngestionJob` tracks attempts
    and records which step failed, which a queue-level retry counter cannot."""

    @staticmethod
    def redis_settings() -> RedisSettings:
        return RedisSettings.from_dsn(str(get_settings().redis_url))


async def enqueue_pool() -> ArqRedis:
    return await create_pool(WorkerSettings.redis_settings())
