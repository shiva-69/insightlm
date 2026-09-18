"""Pipeline gates and anchoring protection, as the domain enforces them."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from insightlm.recruiting.domain.assessment import Application, Stage
from insightlm.recruiting.domain.pipeline import (
    REQUIRED_SCORECARDS_FOR_OFFER,
    GateFacts,
    RejectionReason,
    blockers,
    is_overdue,
    transition,
)
from insightlm.recruiting.domain.scorecard import (
    CriterionRating,
    Recommendation,
    Scorecard,
    visible_to,
)
from insightlm.shared.domain.errors import ConflictError, ValidationError
from insightlm.shared.domain.ids import (
    new_application_id,
    new_candidate_id,
    new_criterion_id,
    new_requisition_id,
    new_scorecard_id,
    new_tenant_id,
    new_user_id,
)
from tests.unit.test_recruiting_domain import APPROVER, criterion, rubric

NOW = datetime(2026, 9, 18, 12, tzinfo=UTC)
RECRUITER = new_user_id()
READY = GateFacts(has_assessment=True, submitted_scorecards=REQUIRED_SCORECARDS_FOR_OFFER)


def application(stage: Stage = Stage.APPLIED, *, entered: datetime = NOW) -> Application:
    return Application(
        id=new_application_id(),
        tenant_id=new_tenant_id(),
        candidate_id=new_candidate_id(),
        requisition_id=new_requisition_id(),
        stage=stage,
        created_at=entered,
        stage_entered_at=entered,
    )


def move(app: Application, to: Stage, facts: GateFacts = READY, **kw: object) -> Application:
    moved, _ = transition(app, to, facts=facts, by=RECRUITER, at=NOW, **kw)  # type: ignore[arg-type]
    return moved


# -- stages ---------------------------------------------------------------------------


def test_the_happy_path_runs_forward_one_stage_at_a_time() -> None:
    app = application()
    for stage in (Stage.SCREENED, Stage.INTERVIEW, Stage.OFFER, Stage.HIRED):
        app = move(app, stage)
    assert app.stage is Stage.HIRED


def test_stages_cannot_be_skipped() -> None:
    with pytest.raises(ConflictError, match="can only move to"):
        move(application(), Stage.INTERVIEW)


def test_nobody_is_screened_without_an_assessment() -> None:
    facts = GateFacts(has_assessment=False, submitted_scorecards=0)
    with pytest.raises(ConflictError, match="assess the candidate"):
        move(application(), Stage.SCREENED, facts)


def test_an_offer_needs_enough_submitted_scorecards() -> None:
    facts = GateFacts(has_assessment=True, submitted_scorecards=1)
    problems = blockers(application(Stage.INTERVIEW), Stage.OFFER, facts)
    assert problems == ["1 more submitted scorecard needed before an offer (1 of 2)"]
    with pytest.raises(ConflictError):
        move(application(Stage.INTERVIEW), Stage.OFFER, facts)


def test_rejection_needs_a_reason_code() -> None:
    with pytest.raises(ValidationError, match="reason code"):
        move(application(), Stage.REJECTED)


def test_a_reason_code_is_only_for_rejection() -> None:
    with pytest.raises(ValidationError):
        move(application(), Stage.SCREENED, reason=RejectionReason.SKILLS_GAP)


def test_rejection_is_allowed_from_any_open_stage_without_gates() -> None:
    nothing = GateFacts(has_assessment=False, submitted_scorecards=0)
    for stage in (Stage.APPLIED, Stage.SCREENED, Stage.INTERVIEW, Stage.OFFER):
        moved = move(application(stage), Stage.REJECTED, nothing, reason=RejectionReason.LOCATION)
        assert moved.stage is Stage.REJECTED


@pytest.mark.parametrize("terminal", [Stage.HIRED, Stage.REJECTED])
def test_terminal_stages_are_final(terminal: Stage) -> None:
    with pytest.raises(ConflictError, match="already"):
        move(application(terminal), Stage.REJECTED, reason=RejectionReason.DUPLICATE)


def test_a_transition_records_who_why_and_resets_the_clock() -> None:
    old = application(entered=NOW - timedelta(days=9))
    moved, record = transition(
        old,
        Stage.REJECTED,
        facts=READY,
        by=RECRUITER,
        at=NOW,
        reason=RejectionReason.SKILLS_GAP,
        note="  No payments background.  ",
    )
    assert moved.stage_entered_at == NOW
    assert (record.from_stage, record.to_stage, record.by) == (
        Stage.APPLIED,
        Stage.REJECTED,
        RECRUITER,
    )
    assert record.reason is RejectionReason.SKILLS_GAP
    assert record.note == "No payments background."


def test_an_application_is_overdue_once_past_its_stage_sla() -> None:
    assert not is_overdue(application(entered=NOW - timedelta(days=2)), NOW)
    assert is_overdue(application(entered=NOW - timedelta(days=4)), NOW)
    # Terminal stages have no clock.
    assert not is_overdue(application(Stage.HIRED, entered=NOW - timedelta(days=400)), NOW)


# -- scorecards -----------------------------------------------------------------------


def card(interviewer: object = None, *, submitted: bool = False) -> Scorecard:
    c = Scorecard(
        id=new_scorecard_id(),
        tenant_id=new_tenant_id(),
        application_id=new_application_id(),
        interviewer_id=interviewer or new_user_id(),  # type: ignore[arg-type]
        assigned_by=RECRUITER,
        assigned_at=NOW,
    )
    if submitted:
        return c.submit(
            by=c.interviewer_id,
            rubric=approved,
            ratings=[],
            recommendation=Recommendation.YES,
            summary="Solid.",
            at=NOW,
        )
    return c


approved = rubric(criterion("Python")).approve(by=APPROVER, at=NOW)


def test_a_panel_member_who_has_not_submitted_sees_no_one_elses_ratings() -> None:
    me = new_user_id()
    cards = [card(me), card(submitted=True), card(submitted=True), card()]
    view = visible_to(me, can_view_scores=True, cards=cards)
    # Even a hiring manager, who may see scores, is blind while their own card is open.
    assert view.others == ()
    assert view.withheld == 2
    assert view.pending == 2
    assert view.own is cards[0]


def test_submitting_unlocks_the_panels_submitted_cards_but_never_drafts() -> None:
    me = new_user_id()
    mine = card(me, submitted=True)
    cards = [mine, card(submitted=True), card()]
    view = visible_to(me, can_view_scores=False, cards=cards)
    assert view.others == (cards[1],)
    assert view.withheld == 0


def test_an_observer_with_score_access_sees_submitted_cards() -> None:
    cards = [card(submitted=True), card()]
    view = visible_to(new_user_id(), can_view_scores=True, cards=cards)
    assert view.others == (cards[0],)


def test_an_outsider_without_score_access_sees_nothing() -> None:
    view = visible_to(new_user_id(), can_view_scores=False, cards=[card(submitted=True)])
    assert (view.own, view.others, view.withheld) == (None, (), 1)


def test_a_submitted_scorecard_is_final() -> None:
    submitted = card(submitted=True)
    with pytest.raises(ConflictError, match="final"):
        submitted.submit(
            by=submitted.interviewer_id,
            rubric=approved,
            ratings=[],
            recommendation=Recommendation.NO,
            summary="Changed my mind after reading the others.",
            at=NOW,
        )


def test_only_the_assigned_interviewer_can_submit() -> None:
    c = card()
    with pytest.raises(ConflictError):
        c.submit(
            by=new_user_id(),
            rubric=approved,
            ratings=[],
            recommendation=Recommendation.YES,
            summary="x",
            at=NOW,
        )


def test_ratings_must_name_criteria_in_the_rubric_once_each() -> None:
    c = card()
    python = approved.criteria[0].id
    for ratings in (
        [CriterionRating(new_criterion_id(), 3)],
        [CriterionRating(python, 3), CriterionRating(python, 2)],
    ):
        with pytest.raises(ValidationError):
            c.submit(
                by=c.interviewer_id,
                rubric=approved,
                ratings=ratings,
                recommendation=Recommendation.YES,
                summary="x",
                at=NOW,
            )


@pytest.mark.parametrize("rating", [0, 5])
def test_ratings_run_one_to_four(rating: int) -> None:
    with pytest.raises(ValidationError):
        CriterionRating(new_criterion_id(), rating)
