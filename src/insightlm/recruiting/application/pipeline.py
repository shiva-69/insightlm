"""Moving applications, assembling panels, and collecting scorecards."""

from __future__ import annotations

from dataclasses import dataclass

from insightlm.governance.domain.audit import AuditAction
from insightlm.recruiting.domain.assessment import Application, Assessment, Candidate, Stage
from insightlm.recruiting.domain.pipeline import (
    GateFacts,
    RejectionReason,
    StageTransition,
    blockers,
    is_overdue,
    next_stage,
    transition,
)
from insightlm.recruiting.domain.rubric import Requisition, Rubric
from insightlm.recruiting.domain.scorecard import (
    CriterionRating,
    Recommendation,
    Scorecard,
    ScorecardView,
    visible_to,
)
from insightlm.recruiting.ports.repositories import (
    RecruitingUnitOfWork,
    RecruitingUnitOfWorkFactory,
)
from insightlm.shared.domain.errors import ConflictError, NotFoundError
from insightlm.shared.domain.ids import ApplicationId, ScorecardId, UserId, new_scorecard_id
from insightlm.shared.domain.policy import Actor, can, require
from insightlm.shared.domain.roles import Permission, Role
from insightlm.shared.ports.clock import Clock

# Roles whose view of recruiting is limited to what they have been assigned to. An
# interviewer invited for one role sees that role's candidates and nothing else.
ASSIGNMENT_SCOPED = frozenset({Role.INTERVIEWER})


def is_assignment_scoped(actor: Actor) -> bool:
    return actor.role in ASSIGNMENT_SCOPED


async def _gate_facts(uow: RecruitingUnitOfWork, application_id: ApplicationId) -> GateFacts:
    cards = await uow.scorecards.list_for_application(application_id)
    return GateFacts(
        has_assessment=await uow.assessments.latest_for(application_id) is not None,
        submitted_scorecards=sum(1 for c in cards if c.is_submitted),
    )


async def _visible_application(
    uow: RecruitingUnitOfWork, actor: Actor, application_id: ApplicationId
) -> Application:
    """The application, or 404 -- including when the actor is scoped away from it.

    404 rather than 403 for the scoped case, for the same reason as across tenants:
    an interviewer should not be able to confirm that a candidate they were not
    assigned to exists.
    """
    application = await uow.applications.get(application_id)
    if application is None:
        raise NotFoundError("application not found")
    if is_assignment_scoped(actor):
        assigned = await uow.scorecards.requisitions_for_interviewer(actor.user_id)
        if application.requisition_id not in assigned:
            raise NotFoundError("application not found")
    return application


class MoveApplication:
    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(
        self,
        actor: Actor,
        application_id: ApplicationId,
        to: Stage,
        *,
        reason: RejectionReason | None = None,
        note: str = "",
    ) -> Application:
        require(actor, Permission.STAGE_ADVANCE)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            application = await _visible_application(uow, actor, application_id)
            moved, record = transition(
                application,
                to,
                facts=await _gate_facts(uow, application_id),
                by=actor.user_id,
                at=self._clock.now(),
                reason=reason,
                note=note,
            )
            await uow.applications.update(moved)
            await uow.transitions.add(record)
            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=AuditAction.STAGE_TRANSITIONED,
                subject_type="application",
                subject_id=str(application_id),
                payload={
                    "from": record.from_stage.value,
                    "to": record.to_stage.value,
                    "reason": record.reason.value if record.reason else None,
                },
            )
            await uow.commit()
        return moved


class AssignInterviewer:
    """Put someone on an application's panel. Their scorecard starts as a draft.

    Whether the person is a member of the workspace who can interview is checked by
    the caller, which can see identity; recruiting only records the assignment.
    """

    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(
        self, actor: Actor, application_id: ApplicationId, interviewer_id: UserId
    ) -> Scorecard:
        require(actor, Permission.STAGE_ADVANCE)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            application = await _visible_application(uow, actor, application_id)
            if application.stage in (Stage.HIRED, Stage.REJECTED):
                raise ConflictError(f"the application is already {application.stage.value}")
            existing = await uow.scorecards.list_for_application(application_id)
            if any(c.interviewer_id == interviewer_id for c in existing):
                raise ConflictError("already on this panel")

            card = Scorecard(
                id=new_scorecard_id(),
                tenant_id=actor.tenant_id,
                application_id=application_id,
                interviewer_id=interviewer_id,
                assigned_by=actor.user_id,
                assigned_at=self._clock.now(),
            )
            await uow.scorecards.add(card, requisition_id=application.requisition_id)
            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=AuditAction.INTERVIEWER_ASSIGNED,
                subject_type="application",
                subject_id=str(application_id),
                payload={"interviewer_id": str(interviewer_id)},
            )
            await uow.commit()
        return card


class SubmitScorecard:
    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(
        self,
        actor: Actor,
        scorecard_id: ScorecardId,
        *,
        ratings: list[CriterionRating],
        recommendation: Recommendation,
        summary: str,
    ) -> Scorecard:
        require(actor, Permission.SCORECARD_SUBMIT)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            card = await uow.scorecards.get(scorecard_id)
            # Someone else's scorecard is not found, not forbidden: its existence is
            # itself a fact about the panel.
            if card is None or card.interviewer_id != actor.user_id:
                raise NotFoundError("scorecard not found")
            application = await uow.applications.get(card.application_id)
            if application is None:
                raise NotFoundError("application not found")
            rubric = await uow.rubrics.approved_for(application.requisition_id)
            if rubric is None:
                raise ConflictError("the requisition has no approved rubric to score against")

            submitted = card.submit(
                by=actor.user_id,
                rubric=rubric,
                ratings=ratings,
                recommendation=recommendation,
                summary=summary,
                at=self._clock.now(),
            )
            await uow.scorecards.update(submitted)
            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=AuditAction.SCORECARD_SUBMITTED,
                subject_type="scorecard",
                subject_id=str(scorecard_id),
                payload={
                    "application_id": str(card.application_id),
                    "recommendation": recommendation.value,
                    "rubric_version": rubric.version,
                },
            )
            await uow.commit()
        return submitted


@dataclass(frozen=True, slots=True)
class ApplicationDetail:
    application: Application
    candidate: Candidate
    requisition: Requisition
    rubric: Rubric | None
    assessment: Assessment | None  # None when the viewer may not see scores
    transitions: list[StageTransition]
    scorecards: ScorecardView
    next_stage: Stage | None
    next_blockers: list[str]
    overdue: bool


class GetApplication:
    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, actor: Actor, application_id: ApplicationId) -> ApplicationDetail:
        require(actor, Permission.CANDIDATE_VIEW)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            application = await _visible_application(uow, actor, application_id)
            candidate = await uow.candidates.get(application.candidate_id)
            requisition = await uow.requisitions.get(application.requisition_id)
            if candidate is None or requisition is None:
                raise NotFoundError("application not found")
            can_view_scores = can(actor, Permission.SCORE_VIEW)
            cards = await uow.scorecards.list_for_application(application_id)
            facts = await _gate_facts(uow, application_id)
            following = next_stage(application.stage)
            return ApplicationDetail(
                application=application,
                candidate=candidate,
                requisition=requisition,
                rubric=await uow.rubrics.approved_for(application.requisition_id),
                assessment=(
                    await uow.assessments.latest_for(application_id) if can_view_scores else None
                ),
                transitions=await uow.transitions.list_for_application(application_id),
                scorecards=visible_to(actor.user_id, can_view_scores=can_view_scores, cards=cards),
                next_stage=following,
                next_blockers=blockers(application, following, facts) if following else [],
                overdue=is_overdue(application, self._clock.now()),
            )


@dataclass(frozen=True, slots=True)
class Interview:
    scorecard: Scorecard
    application: Application
    candidate: Candidate
    requisition: Requisition


class MyInterviews:
    """An interviewer's own queue: the panels they are on, drafts first."""

    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: Actor) -> list[Interview]:
        require(actor, Permission.SCORECARD_SUBMIT)
        interviews: list[Interview] = []
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            for card in await uow.scorecards.list_for_interviewer(actor.user_id):
                application = await uow.applications.get(card.application_id)
                if application is None:
                    continue
                candidate = await uow.candidates.get(application.candidate_id)
                requisition = await uow.requisitions.get(application.requisition_id)
                if candidate is None or requisition is None:
                    continue
                interviews.append(Interview(card, application, candidate, requisition))
        interviews.sort(
            key=lambda i: (i.scorecard.is_submitted, -i.scorecard.assigned_at.timestamp())
        )
        return interviews
