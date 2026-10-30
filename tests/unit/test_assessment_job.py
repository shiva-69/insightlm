"""The assessment job's lifecycle: pure rules, no database."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from insightlm.recruiting.domain.assessment_job import (
    MAX_ATTEMPTS,
    READINESS_PATIENCE,
    AssessmentJob,
    JobStatus,
)
from insightlm.shared.domain.errors import InvariantViolationError
from insightlm.shared.domain.ids import (
    new_application_id,
    new_assessment_id,
    new_assessment_job_id,
    new_tenant_id,
    new_user_id,
)

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)


def queued() -> AssessmentJob:
    return AssessmentJob(
        id=new_assessment_job_id(),
        tenant_id=new_tenant_id(),
        application_id=new_application_id(),
        requested_by=new_user_id(),
        requested_role="recruiter",
        status=JobStatus.QUEUED,
        created_at=NOW,
        updated_at=NOW,
    )


def test_starting_spends_an_attempt_and_clears_any_old_reason() -> None:
    job = queued().wait("waiting for the CV", NOW)
    running = job.start(NOW)
    assert running.status is JobStatus.RUNNING and running.attempts == 1
    assert running.error is None


def test_only_a_queued_job_can_be_started() -> None:
    with pytest.raises(InvariantViolationError):
        queued().start(NOW).start(NOW)


def test_waiting_for_the_cv_gives_the_attempt_back() -> None:
    waiting = queued().start(NOW).wait("waiting for the CV", NOW)
    assert waiting.status is JobStatus.QUEUED and waiting.attempts == 0
    assert waiting.is_active


def test_failures_back_off_then_stop() -> None:
    job = queued()
    delays = []
    for _ in range(MAX_ATTEMPTS):
        job, delay = job.start(NOW).retry_or_fail("provider down", NOW)
        delays.append(delay)
    assert delays == [timedelta(seconds=5), timedelta(seconds=10), None]
    assert job.status is JobStatus.FAILED and not job.is_active


def test_success_records_the_assessment() -> None:
    assessment_id = new_assessment_id()
    done = queued().start(NOW).succeed(assessment_id, NOW)
    assert done.status is JobStatus.SUCCEEDED and done.assessment_id == assessment_id


def test_patience_for_a_cv_runs_out() -> None:
    job = queued()
    assert not job.waited_too_long(NOW + READINESS_PATIENCE)
    assert job.waited_too_long(NOW + READINESS_PATIENCE + timedelta(seconds=1))
