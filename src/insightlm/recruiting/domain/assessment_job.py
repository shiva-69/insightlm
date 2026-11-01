"""Assessment as a job: requested now, run by the worker, recorded either way.

An assessment makes one model call per criterion and takes seconds. Run inside the web
request, a batch of fifty depended on a browser tab staying open, and a slow provider
held a request open long enough to look like an outage. As a job it is requested in a
transaction -- the job row and an outbox event commit together, exactly like an upload
-- and the worker does the rest.

The job carries what the worker needs to act *as the person who asked*: their user id
and the role they held. The worker re-checks the permission with that role, so a job
can never do more than its requester could. The cost is the same one ADR-0003 accepts
for tokens: a role change takes effect for jobs requested afterwards.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Self

from insightlm.shared.domain.errors import InvariantViolationError
from insightlm.shared.domain.ids import (
    ApplicationId,
    AssessmentId,
    AssessmentJobId,
    TenantId,
    UserId,
)

MAX_ATTEMPTS = 3
RETRY_BASE = timedelta(seconds=5)
READINESS_WAIT = timedelta(seconds=10)
"""How long to wait before checking again when the candidate's CV is still being read."""
READINESS_PATIENCE = timedelta(minutes=30)
"""After this long waiting for a CV, give up and say so rather than wait for ever."""


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Readiness(StrEnum):
    """Whether a candidate's documents can be assessed yet."""

    READY = "ready"
    PROCESSING = "processing"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class AssessmentJob:
    id: AssessmentJobId
    tenant_id: TenantId
    application_id: ApplicationId
    requested_by: UserId
    requested_role: str
    status: JobStatus
    created_at: datetime
    updated_at: datetime
    attempts: int = 0
    assessment_id: AssessmentId | None = None
    error: str | None = None

    @property
    def is_active(self) -> bool:
        """Queued or running: a second request for the same application joins this one."""
        return self.status in (JobStatus.QUEUED, JobStatus.RUNNING)

    def start(self, now: datetime) -> Self:
        if self.status is not JobStatus.QUEUED:
            raise InvariantViolationError(f"cannot start a {self.status.value} job")
        return replace(
            self, status=JobStatus.RUNNING, attempts=self.attempts + 1, error=None, updated_at=now
        )

    def succeed(self, assessment_id: AssessmentId, now: datetime) -> Self:
        return replace(
            self,
            status=JobStatus.SUCCEEDED,
            assessment_id=assessment_id,
            error=None,
            updated_at=now,
        )

    def wait(self, reason: str, now: datetime) -> Self:
        """Back to the queue without spending an attempt: nothing failed, it is early."""
        return replace(
            self,
            status=JobStatus.QUEUED,
            attempts=max(self.attempts - 1, 0),
            error=reason,
            updated_at=now,
        )

    def retry_or_fail(self, error: str, now: datetime) -> tuple[Self, timedelta | None]:
        """A failure worth another try is queued again with backoff, until attempts run out."""
        if self.attempts < MAX_ATTEMPTS:
            delay = RETRY_BASE * (2 ** (self.attempts - 1))
            return replace(self, status=JobStatus.QUEUED, error=error, updated_at=now), delay
        return self.fail(error, now), None

    def fail(self, error: str, now: datetime) -> Self:
        return replace(self, status=JobStatus.FAILED, error=error, updated_at=now)

    def waited_too_long(self, now: datetime) -> bool:
        return now - self.created_at > READINESS_PATIENCE
