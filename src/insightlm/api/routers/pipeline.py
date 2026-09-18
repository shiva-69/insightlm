"""Stages, interview panels, scorecards, and talent-pool search."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import Field

from insightlm.api.deps import ActorDep, ContainerDep
from insightlm.api.routers.recruiting import (
    ApplicationResponse,
    AssessmentResponse,
    CandidateResponse,
    RequisitionResponse,
    RubricResponse,
    _In,
    _Out,
)
from insightlm.recruiting.domain.assessment import Stage
from insightlm.recruiting.domain.pipeline import RejectionReason
from insightlm.recruiting.domain.scorecard import CriterionRating, Recommendation
from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.domain.ids import ApplicationId, CriterionId, ScorecardId, UserId
from insightlm.shared.domain.roles import ROLE_PERMISSIONS, Permission

router = APIRouter(tags=["pipeline"])


# -- requests ------------------------------------------------------------------------


class MoveRequest(_In):
    to: Stage
    reason: RejectionReason | None = None
    note: str = Field(default="", max_length=2000)


class AssignRequest(_In):
    user_id: UUID


class RatingRequest(_In):
    criterion_id: UUID
    rating: int = Field(ge=1, le=4)
    note: str = Field(default="", max_length=2000)


class SubmitScorecardRequest(_In):
    ratings: list[RatingRequest] = Field(max_length=25)
    recommendation: Recommendation
    summary: str = Field(min_length=1, max_length=4000)


# -- responses -----------------------------------------------------------------------


class RatingResponse(_Out):
    criterion_id: UUID
    rating: int
    note: str


class ScorecardResponse(_Out):
    id: UUID
    application_id: UUID
    interviewer_id: UUID
    assigned_by: UUID
    assigned_at: datetime
    ratings: list[RatingResponse]
    recommendation: Recommendation | None
    summary: str
    submitted_at: datetime | None
    is_submitted: bool


class ScorecardViewResponse(_Out):
    own: ScorecardResponse | None
    others: list[ScorecardResponse]
    withheld: int = Field(description="Submitted scorecards withheld until you submit yours.")
    pending: int


class TransitionResponse(_Out):
    from_stage: Stage
    to_stage: Stage
    by: UUID
    at: datetime
    reason: RejectionReason | None
    note: str


class ApplicationDetailResponse(_Out):
    application: ApplicationResponse
    candidate: CandidateResponse
    requisition: RequisitionResponse
    rubric: RubricResponse | None
    assessment: AssessmentResponse | None
    transitions: list[TransitionResponse]
    scorecards: ScorecardViewResponse
    next_stage: Stage | None
    next_blockers: list[str]
    overdue: bool


class InterviewResponse(_Out):
    scorecard: ScorecardResponse
    application: ApplicationResponse
    candidate: CandidateResponse
    requisition: RequisitionResponse


class PassageResponse(_Out):
    label: str
    document_id: UUID
    document_title: str
    page: int
    text: str
    score: float


class TalentMatchResponse(_Out):
    candidate: CandidateResponse
    passages: list[PassageResponse]


# -- routes --------------------------------------------------------------------------


@router.get(
    "/applications/{application_id}",
    response_model=ApplicationDetailResponse,
    summary="An application, its history, and what is blocking its next move",
)
async def get_application(
    application_id: UUID, actor: ActorDep, container: ContainerDep
) -> ApplicationDetailResponse:
    detail = await container.get_application.execute(actor, ApplicationId(application_id))
    return ApplicationDetailResponse.model_validate(detail)


@router.post(
    "/applications/{application_id}/stage",
    response_model=ApplicationResponse,
    summary="Move an application; gates are enforced and rejection needs a reason code",
)
async def move_application(
    application_id: UUID, body: MoveRequest, actor: ActorDep, container: ContainerDep
) -> ApplicationResponse:
    moved = await container.move_application.execute(
        actor, ApplicationId(application_id), body.to, reason=body.reason, note=body.note
    )
    return ApplicationResponse.model_validate(moved)


@router.post(
    "/applications/{application_id}/panel",
    response_model=ScorecardResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Add an interviewer to the panel; their scorecard starts as a draft",
)
async def assign_interviewer(
    application_id: UUID, body: AssignRequest, actor: ActorDep, container: ContainerDep
) -> ScorecardResponse:
    # Recruiting records assignments; identity knows who can interview. Checked here,
    # where both are visible.
    members = await container.list_members.execute(actor)
    member = next(
        (m for m in members if m.user.id == body.user_id and m.membership.is_active), None
    )
    if member is None:
        raise ValidationError("that person is not an active member of this workspace")
    if Permission.SCORECARD_SUBMIT not in ROLE_PERMISSIONS[member.membership.role]:
        raise ValidationError(f"a {member.membership.role.value} cannot submit scorecards")

    card = await container.assign_interviewer.execute(
        actor, ApplicationId(application_id), UserId(body.user_id)
    )
    return ScorecardResponse.model_validate(card)


@router.post(
    "/scorecards/{scorecard_id}/submit",
    response_model=ScorecardResponse,
    summary="Submit your scorecard. Final once submitted",
)
async def submit_scorecard(
    scorecard_id: UUID, body: SubmitScorecardRequest, actor: ActorDep, container: ContainerDep
) -> ScorecardResponse:
    card = await container.submit_scorecard.execute(
        actor,
        ScorecardId(scorecard_id),
        ratings=[
            CriterionRating(
                criterion_id=CriterionId(r.criterion_id), rating=r.rating, note=r.note.strip()
            )
            for r in body.ratings
        ],
        recommendation=body.recommendation,
        summary=body.summary,
    )
    return ScorecardResponse.model_validate(card)


@router.get(
    "/me/interviews",
    response_model=list[InterviewResponse],
    summary="Panels you are on, scorecards still to write first",
)
async def my_interviews(actor: ActorDep, container: ContainerDep) -> list[InterviewResponse]:
    return [
        InterviewResponse.model_validate(i) for i in await container.my_interviews.execute(actor)
    ]


@router.get(
    "/talent/search",
    response_model=list[TalentMatchResponse],
    summary="Search every candidate's documents; returns people with the passage that matched",
)
async def search_talent(
    actor: ActorDep,
    container: ContainerDep,
    q: str = Query(min_length=1, max_length=500),
    limit: int = Query(default=10, ge=1, le=50),
) -> list[TalentMatchResponse]:
    matches = await container.search_talent_pool.execute(actor, q, limit=limit)
    return [
        TalentMatchResponse(
            candidate=CandidateResponse.model_validate(m.candidate),
            passages=[
                PassageResponse(
                    label=s.label,
                    document_id=s.document_id,
                    document_title=s.document_title,
                    page=s.page,
                    text=s.text,
                    score=s.score,
                )
                for s in m.passages
            ],
        )
        for m in matches
    ]
