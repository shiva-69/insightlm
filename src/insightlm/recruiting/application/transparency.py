"""What we hold about a candidate, and how long we keep it.

**The record** is everything the system holds about one person, in one place: what they
sent, every application and where it stands, every assessment with the evidence each
verdict rests on, submitted scorecards, and what they chose to self-identify. It is what
a subject-access request is answered with, and it's generated from the same stores
erasure clears, so the two can't drift apart.

**Retention** decides who is past it: a candidate whose every application is closed as
rejected, and whose last activity is older than the retention period. Hired candidates
are not purged here; their data moves under employment records, which is a different
policy. The purge itself is erasure, with the same verification.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from insightlm.recruiting.domain.assessment import Application, Assessment, Candidate, Stage
from insightlm.recruiting.domain.fairness import Dimension, SelfIdentification
from insightlm.recruiting.domain.pipeline import StageTransition
from insightlm.recruiting.domain.rubric import Requisition
from insightlm.recruiting.domain.scorecard import Scorecard
from insightlm.recruiting.ports.repositories import RecruitingUnitOfWorkFactory
from insightlm.shared.domain.errors import NotFoundError
from insightlm.shared.domain.ids import CandidateId
from insightlm.shared.domain.policy import Actor, require
from insightlm.shared.domain.roles import PlatformPermission
from insightlm.shared.ports.clock import Clock


@dataclass(frozen=True, slots=True)
class ApplicationRecord:
    application: Application
    requisition: Requisition | None
    transitions: list[StageTransition]
    assessment: Assessment | None
    scorecards: list[Scorecard]  # submitted only: a draft is not yet an opinion held


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    candidate: Candidate
    applications: list[ApplicationRecord]
    self_identification: list[SelfIdentification]


class GetCandidateRecord:
    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: Actor, candidate_id: CandidateId) -> CandidateRecord:
        require(actor, PlatformPermission.TENANT_MANAGE)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            candidate = await uow.candidates.get(candidate_id)
            if candidate is None:
                raise NotFoundError("candidate not found")
            applications = []
            for application in await uow.applications.list_for_candidate(candidate_id):
                cards = await uow.scorecards.list_for_application(application.id)
                applications.append(
                    ApplicationRecord(
                        application=application,
                        requisition=await uow.requisitions.get(application.requisition_id),
                        transitions=await uow.transitions.list_for_application(application.id),
                        assessment=await uow.assessments.latest_for(application.id),
                        scorecards=[c for c in cards if c.is_submitted],
                    )
                )
            identities = [
                identity
                for dimension in Dimension
                if (
                    identity := (
                        await uow.self_identification.for_candidates([candidate_id], dimension)
                    ).get(candidate_id)
                )
            ]
        return CandidateRecord(candidate, applications, identities)


@dataclass(frozen=True, slots=True)
class Expired:
    candidate_id: CandidateId
    last_activity: datetime


def is_expired(
    applications: list[Application],
    transitions: list[StageTransition],
    *,
    created_at: datetime,
    now: datetime,
    retention: timedelta,
) -> tuple[bool, datetime]:
    """Past retention: every application rejected, and nothing has happened for too long."""
    last = max([created_at, *(a.created_at for a in applications), *(t.at for t in transitions)])
    closed = bool(applications) and all(a.stage is Stage.REJECTED for a in applications)
    return closed and now - last > retention, last


class FindExpiredCandidates:
    def __init__(
        self, uow_factory: RecruitingUnitOfWorkFactory, clock: Clock, retention: timedelta
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._retention = retention

    async def execute(self, actor: Actor) -> list[Expired]:
        require(actor, PlatformPermission.TENANT_MANAGE)
        now = self._clock.now()
        expired: list[Expired] = []
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            for candidate in await uow.candidates.list_for_tenant(actor.tenant_id):
                applications = await uow.applications.list_for_candidate(candidate.id)
                transitions = [
                    t
                    for a in applications
                    for t in await uow.transitions.list_for_application(a.id)
                ]
                gone, last = is_expired(
                    applications,
                    transitions,
                    created_at=candidate.created_at,
                    now=now,
                    retention=self._retention,
                )
                if gone:
                    expired.append(Expired(candidate.id, last))
        return expired
