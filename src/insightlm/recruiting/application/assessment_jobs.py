"""Requesting assessments, and running them in the worker.

``RequestAssessment`` checks everything that can be checked cheaply -- the permission,
that the application exists, that a human has approved the rubric -- and then commits a
job and an outbox event together. Nothing is spent until the worker runs it, and a
second request for the same application joins the job already queued rather than paying
twice.

``ProcessAssessmentJob`` is what the worker runs. It waits for the candidate's CV to be
fully read, acts as the person who asked, and sorts failures into the two kinds that
need different handling: a provider hiccup is retried with backoff, a refusal (no
approved rubric, no permission, budget spent) is final and recorded with its reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from insightlm.recruiting.application.assess import RunAssessment
from insightlm.recruiting.domain.assessment import Assessment, Stage
from insightlm.recruiting.domain.assessment_job import (
    READINESS_WAIT,
    AssessmentJob,
    JobStatus,
    Readiness,
)
from insightlm.recruiting.domain.audit_actions import RecruitingAudit
from insightlm.recruiting.domain.roles import RecruitingPermission
from insightlm.recruiting.ports.documents import DocumentReadiness
from insightlm.recruiting.ports.repositories import (
    RecruitingUnitOfWork,
    RecruitingUnitOfWorkFactory,
)
from insightlm.shared.domain.errors import (
    AuthenticationError,
    BudgetExceededError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from insightlm.shared.domain.ids import (
    ApplicationId,
    AssessmentId,
    AssessmentJobId,
    RequisitionId,
    TenantId,
    new_assessment_job_id,
    new_outbox_event_id,
)
from insightlm.shared.domain.policy import Actor, require
from insightlm.shared.domain.roles import RoleCatalogue
from insightlm.shared.observability.logging import get_logger
from insightlm.shared.ports.clock import Clock
from insightlm.shared.ports.outbox import OutboxEvent

_logger = get_logger(__name__)

ASSESSMENT_REQUESTED = "assessment.requested"
"""The outbox event the worker turns into a job run."""

# Refusals: running the job again would be refused again, so it fails at once.
_FINAL = (
    AuthenticationError,
    BudgetExceededError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)


async def _enqueue(
    uow: RecruitingUnitOfWork, actor: Actor, application_id: ApplicationId, clock: Clock
) -> AssessmentJob:
    """Join the active job for this application, or stage a new one. Caller commits."""
    existing = await uow.assessment_jobs.active_for(application_id)
    if existing is not None:
        return existing
    now = clock.now()
    job = AssessmentJob(
        id=new_assessment_job_id(),
        tenant_id=actor.tenant_id,
        application_id=application_id,
        requested_by=actor.user_id,
        requested_role=actor.role.value,
        status=JobStatus.QUEUED,
        created_at=now,
        updated_at=now,
    )
    await uow.assessment_jobs.add(job)
    uow.outbox.add(
        OutboxEvent(
            id=new_outbox_event_id(),
            tenant_id=actor.tenant_id,
            event_type=ASSESSMENT_REQUESTED,
            payload={"assessment_job_id": str(job.id), "application_id": str(application_id)},
            occurred_at=now,
        )
    )
    await uow.audit.record(
        tenant_id=actor.tenant_id,
        actor_id=actor.user_id,
        action=RecruitingAudit.ASSESSMENT_REQUESTED,
        subject_type="application",
        subject_id=str(application_id),
        payload={"assessment_job_id": str(job.id)},
    )
    return job


class RequestAssessment:
    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, actor: Actor, application_id: ApplicationId) -> AssessmentJob:
        require(actor, RecruitingPermission.ASSESSMENT_RUN)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            application = await uow.applications.get(application_id)
            if application is None:
                raise NotFoundError("application not found")
            if await uow.rubrics.approved_for(application.requisition_id) is None:
                # Refused here, not later in the worker: the person asking should hear it
                # now, and a job that can only fail should never be queued.
                raise ConflictError(
                    "this requisition has no approved rubric; a human must approve the "
                    "criteria before anyone is assessed against them"
                )
            job = await _enqueue(uow, actor, application_id, self._clock)
            await uow.commit()
        return job


class BatchScope(StrEnum):
    UNASSESSED = "unassessed"
    """Applications with no assessment yet."""
    STALE = "stale"
    """Unassessed, or assessed under an older version of the rubric."""
    ALL = "all"


_CLOSED = {Stage.HIRED, Stage.REJECTED}


@dataclass(frozen=True, slots=True)
class BatchResult:
    jobs: tuple[AssessmentJob, ...]
    skipped_current: int
    """Already assessed under the rubric in force, and out of scope."""
    skipped_closed: int
    """Hired or rejected: assessing them again would spend money to change nothing."""


class RequestAssessments:
    """Queue assessments for a whole requisition -- after a bulk upload, or when the
    rubric changes and existing scores were made under criteria no longer in force."""

    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(
        self, actor: Actor, requisition_id: RequisitionId, scope: BatchScope
    ) -> BatchResult:
        require(actor, RecruitingPermission.ASSESSMENT_RUN)
        jobs: list[AssessmentJob] = []
        current = closed = 0
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            if await uow.requisitions.get(requisition_id) is None:
                raise NotFoundError("requisition not found")
            rubric = await uow.rubrics.approved_for(requisition_id)
            if rubric is None:
                raise ConflictError("this requisition has no approved rubric")
            for application in await uow.applications.list_for_requisition(requisition_id):
                if application.stage in _CLOSED:
                    closed += 1
                    continue
                latest = await uow.assessments.latest_for(application.id)
                in_scope = (
                    scope is BatchScope.ALL
                    or latest is None
                    or (scope is BatchScope.STALE and latest.rubric_version < rubric.version)
                )
                if not in_scope:
                    current += 1
                    continue
                jobs.append(await _enqueue(uow, actor, application.id, self._clock))
            await uow.commit()
        return BatchResult(jobs=tuple(jobs), skipped_current=current, skipped_closed=closed)


class GetAssessmentJob:
    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: Actor, job_id: AssessmentJobId) -> AssessmentJob:
        require(actor, RecruitingPermission.SCORE_VIEW)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            job = await uow.assessment_jobs.get(job_id)
        if job is None:
            raise NotFoundError("assessment job not found")
        return job


class GetAssessment:
    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: Actor, assessment_id: AssessmentId) -> Assessment:
        require(actor, RecruitingPermission.SCORE_VIEW)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            assessment = await uow.assessments.get(assessment_id)
        if assessment is None:
            raise NotFoundError("assessment not found")
        return assessment


class Outcome(StrEnum):
    SUCCEEDED = "succeeded"
    WAITING = "waiting"
    """The CV is still being read; check again shortly. No attempt was spent."""
    RETRYING = "retrying"
    FAILED = "failed"
    SKIPPED = "skipped"
    """Nothing to do: the job is finished, gone, or already running elsewhere."""


@dataclass(frozen=True, slots=True)
class JobOutcome:
    outcome: Outcome
    job: AssessmentJob | None = None
    retry_in: timedelta | None = None
    assessment_id: AssessmentId | None = None
    error: Exception | None = None
    """The exception behind a failure, for a caller waiting inline to re-raise. Not stored."""


class ProcessAssessmentJob:
    def __init__(
        self,
        uow_factory: RecruitingUnitOfWorkFactory,
        run_assessment: RunAssessment,
        readiness: DocumentReadiness,
        roles: RoleCatalogue,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._run = run_assessment
        self._readiness = readiness
        self._roles = roles
        self._clock = clock

    async def execute(self, tenant_id: TenantId, job_id: AssessmentJobId) -> JobOutcome:
        # Claim the job: QUEUED -> RUNNING in its own short transaction, so a crash
        # mid-assessment leaves a RUNNING row for the reaper rather than nothing.
        async with self._uow_factory(tenant_id=tenant_id) as uow:
            job = await uow.assessment_jobs.get(job_id)
            if job is None or job.status is not JobStatus.QUEUED:
                return JobOutcome(Outcome.SKIPPED, job)
            application = await uow.applications.get(job.application_id)
            candidate = await uow.candidates.get(application.candidate_id) if application else None
            if application is None or candidate is None:
                failed = job.fail("the application no longer exists", self._clock.now())
                await uow.assessment_jobs.update(failed)
                await uow.commit()
                return JobOutcome(Outcome.FAILED, failed)
            running = job.start(self._clock.now())
            await uow.assessment_jobs.update(running)
            await uow.commit()

        readiness = await self._readiness.readiness(tenant_id, candidate.document_ids)
        if readiness is Readiness.FAILED:
            return await self._finish(
                running.fail("the candidate's CV could not be read", self._clock.now())
            )
        if readiness is Readiness.PROCESSING:
            now = self._clock.now()
            if running.waited_too_long(now):
                return await self._finish(
                    running.fail("the candidate's CV was never ready to assess", now)
                )
            waiting = running.wait("waiting for the CV to finish processing", now)
            return await self._finish(waiting, Outcome.WAITING, READINESS_WAIT)

        actor = Actor.of(
            self._roles,
            user_id=running.requested_by,
            tenant_id=tenant_id,
            role=self._roles.resolve(running.requested_role),
        )
        try:
            assessment = await self._run.execute(actor, running.application_id)
        except _FINAL as exc:
            return await self._finish(running.fail(str(exc), self._clock.now()), error=exc)
        except Exception as exc:  # provider errors, timeouts, the unexpected
            _logger.warning("assessment_job_failed", job_id=str(job_id), error=repr(exc))
            retried, delay = running.retry_or_fail(
                f"{type(exc).__name__}: {exc}", self._clock.now()
            )
            outcome = Outcome.RETRYING if delay is not None else Outcome.FAILED
            return await self._finish(retried, outcome, delay, error=exc)
        return await self._finish(
            running.succeed(assessment.id, self._clock.now()),
            Outcome.SUCCEEDED,
            assessment_id=assessment.id,
        )

    async def _finish(
        self,
        job: AssessmentJob,
        outcome: Outcome = Outcome.FAILED,
        retry_in: timedelta | None = None,
        *,
        assessment_id: AssessmentId | None = None,
        error: Exception | None = None,
    ) -> JobOutcome:
        async with self._uow_factory(tenant_id=job.tenant_id) as uow:
            await uow.assessment_jobs.update(job)
            await uow.commit()
        return JobOutcome(outcome, job, retry_in, assessment_id, error)
