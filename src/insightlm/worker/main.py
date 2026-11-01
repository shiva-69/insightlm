"""ARQ worker entrypoint.

Same codebase as the API, different entrypoint: same domain, same unit of work, same
rules. What differs is only what it is asked to do.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar
from uuid import UUID

from arq import create_pool
from arq.connections import ArqRedis, RedisSettings
from arq.cron import cron
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from insightlm.bootstrap import Container, build_container
from insightlm.recruiting.application.assessment_jobs import ASSESSMENT_REQUESTED, Outcome
from insightlm.settings import get_settings
from insightlm.shared.adapters.db.relay import OutboxRelay
from insightlm.shared.domain.ids import AssessmentJobId, DocumentVersionId, TenantId
from insightlm.shared.observability.logging import configure_logging, get_logger
from insightlm.shared.observability.telemetry import (
    bind_tenant,
    configure_telemetry,
    continued_from,
    tracer,
)
from insightlm.worker.ingestion import MAX_ATTEMPTS

_logger = get_logger(__name__)

INGEST_TASK = "ingest_document_version"
ASSESS_TASK = "run_assessment_job"

# Event type -> (task, the payload field the task is called with).
_EVENT_TASKS: dict[str, tuple[str, str]] = {
    "document.ingest_requested": (INGEST_TASK, "document_version_id"),
    ASSESSMENT_REQUESTED: (ASSESS_TASK, "assessment_job_id"),
}


async def startup(ctx: dict[str, Any]) -> None:
    settings = get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)
    configure_telemetry(
        enabled=settings.otel_enabled,
        endpoint=settings.otel_endpoint,
        service="insightlm-worker",
        environment=settings.environment,
    )
    ctx["container"] = build_container(settings)
    admin = create_async_engine(str(settings.database_url), pool_size=1, max_overflow=0)
    ctx["admin_engine"] = admin
    ctx["admin_sessions"] = async_sessionmaker(bind=admin, expire_on_commit=False)
    await ctx["container"].object_store.ensure_bucket()
    _logger.info("worker_started")


async def shutdown(ctx: dict[str, Any]) -> None:
    container: Container = ctx["container"]
    await container.dispose()
    await ctx["admin_engine"].dispose()
    _logger.info("worker_stopped")


async def ingest_document_version(
    ctx: dict[str, Any],
    tenant_id: str,
    document_version_id: str,
    trace: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run the ingestion pipeline for one document version.

    Idempotent: the pipeline resumes from the job's recorded steps, so a redelivered
    message re-runs at most the step that was in flight.
    """
    container: Container = ctx["container"]
    # Continue the upload request's trace: the event carried its context through the
    # outbox, so these spans appear under the request that caused them.
    with continued_from(trace), tracer.start_as_current_span("ingest_document_version") as span:
        span.set_attribute("insightlm.document_version_id", document_version_id)
        bind_tenant(tenant_id)
        outcome = await container.documents.ingestion.run(
            tenant_id=TenantId(UUID(tenant_id)),
            version_id=DocumentVersionId(UUID(document_version_id)),
        )
    if outcome.retry_in is not None:
        # The pipeline decided this failure is worth another attempt; the queue only
        # schedules it. Without this, a failed step was recorded and never retried,
        # and the document sat at "processing" forever.
        redis: ArqRedis = ctx["redis"]
        await redis.enqueue_job(
            "ingest_document_version",
            tenant_id,
            document_version_id,
            _defer_by=timedelta(seconds=outcome.retry_in),
            trace=trace,  # the retry belongs to the same trace as the first attempt
        )
        _logger.warning(
            "ingestion_retry_scheduled", version_id=document_version_id, in_s=outcome.retry_in
        )
    return {
        "completed": outcome.completed,
        "steps_run": [step.value for step in outcome.steps_run],
        "dead_lettered": outcome.dead_lettered,
        "error": outcome.error,
        "retry_in": outcome.retry_in,
    }


async def run_assessment_job(
    ctx: dict[str, Any],
    tenant_id: str,
    assessment_job_id: str,
    trace: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Run one assessment job. Safe to deliver twice: only a QUEUED job is claimed."""
    container: Container = ctx["container"]
    with continued_from(trace), tracer.start_as_current_span("run_assessment_job") as span:
        span.set_attribute("insightlm.assessment_job_id", assessment_job_id)
        bind_tenant(tenant_id)
        result = await container.recruiting.process_assessment_job.execute(
            TenantId(UUID(tenant_id)), AssessmentJobId(UUID(assessment_job_id))
        )
    if result.retry_in is not None:
        # Waiting for the CV, or backing off after a provider error: the use case
        # decided when to look again, the queue only schedules it.
        redis: ArqRedis = ctx["redis"]
        await redis.enqueue_job(
            ASSESS_TASK, tenant_id, assessment_job_id, _defer_by=result.retry_in, trace=trace
        )
    if result.outcome in (Outcome.FAILED, Outcome.RETRYING):
        _logger.warning(
            "assessment_job_not_done",
            job_id=assessment_job_id,
            outcome=result.outcome.value,
            error=result.job.error if result.job else None,
        )
    return {
        "outcome": result.outcome.value,
        "assessment_id": str(result.assessment_id) if result.assessment_id else None,
        "retry_in": result.retry_in.total_seconds() if result.retry_in else None,
    }


STALLED_SQL = text(
    """
    SELECT tenant_id, document_version_id, attempts
    FROM ingestion_jobs
    WHERE (status = 'failed' AND attempts < :max_attempts
           AND updated_at < now() - interval '1 minute')
       OR (status IN ('pending', 'running') AND updated_at < now() - interval '15 minutes')
    ORDER BY updated_at
    LIMIT 100
    """
)


async def stalled_jobs(session: AsyncSession) -> list[tuple[str, str, int]]:
    rows = await session.execute(STALLED_SQL, {"max_attempts": MAX_ATTEMPTS})
    return [(str(t), str(v), int(a)) for t, v, a in rows]


# A queued assessment is normally picked up within one outbox cycle (10 s), and a
# deferred retry within a minute. Queued for five minutes means the message was lost;
# running for fifteen means the worker running it died. Either way, put it back.
STALLED_ASSESSMENTS_SQL = text(
    """
    UPDATE assessment_jobs SET status = 'queued', updated_at = now()
    WHERE (status = 'queued' AND updated_at < now() - interval '5 minutes')
       OR (status = 'running' AND updated_at < now() - interval '15 minutes')
    RETURNING tenant_id, id, attempts
    """
)


async def stalled_assessments(session: AsyncSession) -> list[tuple[str, str, int]]:
    rows = await session.execute(STALLED_ASSESSMENTS_SQL)
    found = [(str(t), str(j), int(a)) for t, j, a in rows]
    await session.commit()
    return found


async def requeue_stalled(ctx: dict[str, Any]) -> int:
    """Re-enqueue ingestion that failed and was never retried, or that stopped moving.

    A retry is scheduled by the worker that saw the failure. If that worker dies, is
    replaced by a deploy, or predates the retry logic, nothing else would ever run the
    job again and the document sits at "processing" for good. That happened: eight
    documents were found stuck this way. This is the backstop.

    Cross-tenant by nature, so it reads through the privileged engine -- one SELECT of
    ids, the same exemption the outbox relay has (ADR-0016). The job itself then runs
    under the tenant's own RLS context like any other. The job id includes the attempt
    number, so two sweeps can't enqueue the same attempt twice.
    """
    redis: ArqRedis = ctx["redis"]
    async with ctx["admin_sessions"]() as session:
        stalled = await stalled_jobs(session)
        assessments = await stalled_assessments(session)
    for tenant_id, version_id, attempts in stalled:
        await redis.enqueue_job(
            INGEST_TASK, tenant_id, version_id, _job_id=f"reap:{version_id}:{attempts}"
        )
    for tenant_id, job_id, attempts in assessments:
        await redis.enqueue_job(ASSESS_TASK, tenant_id, job_id, _job_id=f"reap:{job_id}:{attempts}")
    if stalled:
        _logger.warning("ingestion_requeued", count=len(stalled))
    if assessments:
        _logger.warning("assessments_requeued", count=len(assessments))
    return len(stalled) + len(assessments)


async def drain_outbox(ctx: dict[str, Any]) -> int:
    """Publish staged domain events onto the queue.

    Scheduled rather than triggered by writers: a relay that must be called is a relay
    that will eventually be forgotten.
    """
    container: Container = ctx["container"]
    redis: ArqRedis = ctx["redis"]

    async def publish(
        event_type: str, payload: dict[str, Any], tenant_id: str, attributes: dict[str, Any]
    ) -> None:
        route = _EVENT_TASKS.get(event_type)
        if route is None:
            return  # nothing consumes this event type yet
        task, key = route
        await redis.enqueue_job(task, tenant_id, payload[key], trace=attributes.get("trace"))

    published = await OutboxRelay(container.session_factory, publisher=publish).drain()
    if published:
        _logger.info("outbox_drained", published=published)
    return published


class WorkerSettings:
    """ARQ reads these attributes off the class, so they are ClassVars by contract."""

    functions: ClassVar[list[Any]] = [ingest_document_version, run_assessment_job]
    cron_jobs: ClassVar[list[Any]] = [
        cron(drain_outbox, second={0, 10, 20, 30, 40, 50}, run_at_startup=True),
        cron(requeue_stalled, second={30}, run_at_startup=False),
    ]
    on_startup = startup
    on_shutdown = shutdown
    max_tries = 1
    """Retries are the pipeline's job, not the queue's: `IngestionJob` tracks attempts
    and records which step failed, which a queue-level retry counter cannot."""

    # A plain attribute, evaluated at import. ARQ reads this off the class directly,
    # so a @staticmethod or @property hands it the descriptor object instead of the
    # settings and the worker dies on startup with an unhelpful AttributeError.
    # Importing this module therefore requires the environment to be configured --
    # which is true of a worker entrypoint by definition.
    redis_settings: ClassVar[RedisSettings] = RedisSettings.from_dsn(str(get_settings().redis_url))


async def enqueue_pool() -> ArqRedis:
    """A client pool for enqueuing from outside the worker."""
    return await create_pool(WorkerSettings.redis_settings)
