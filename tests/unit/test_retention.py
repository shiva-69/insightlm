"""Who is past retention."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from insightlm.recruiting.application.transparency import is_expired
from insightlm.recruiting.domain.assessment import Application, Stage
from insightlm.recruiting.domain.pipeline import StageTransition
from insightlm.shared.domain.ids import (
    new_application_id,
    new_candidate_id,
    new_requisition_id,
    new_tenant_id,
    new_user_id,
)

NOW = datetime(2026, 9, 19, tzinfo=UTC)
YEAR = timedelta(days=365)


def app(stage: Stage, at: datetime) -> Application:
    return Application(
        new_application_id(),
        new_tenant_id(),
        new_candidate_id(),
        new_requisition_id(),
        stage,
        at,
        at,
    )


def moved(application: Application, at: datetime) -> StageTransition:
    return StageTransition(
        application.tenant_id, application.id, Stage.APPLIED, Stage.REJECTED, new_user_id(), at
    )


def test_rejected_and_quiet_for_longer_than_retention_is_expired() -> None:
    old = NOW - timedelta(days=400)
    a = app(Stage.REJECTED, old)
    assert is_expired([a], [moved(a, old)], created_at=old, now=NOW, retention=YEAR)[0]


def test_recent_activity_restarts_the_clock() -> None:
    old = NOW - timedelta(days=400)
    a = app(Stage.REJECTED, old)
    recent = moved(a, NOW - timedelta(days=10))
    assert not is_expired([a], [recent], created_at=old, now=NOW, retention=YEAR)[0]


def test_any_open_application_keeps_the_candidate() -> None:
    old = NOW - timedelta(days=400)
    apps = [app(Stage.REJECTED, old), app(Stage.INTERVIEW, old)]
    assert not is_expired(apps, [], created_at=old, now=NOW, retention=YEAR)[0]


def test_hired_candidates_are_not_purged_by_recruiting_retention() -> None:
    old = NOW - timedelta(days=4000)
    assert not is_expired([app(Stage.HIRED, old)], [], created_at=old, now=NOW, retention=YEAR)[0]
