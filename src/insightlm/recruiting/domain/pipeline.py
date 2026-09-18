"""The hiring pipeline: which moves are allowed, and what each one requires.

Gates are domain rules, not frontend validation. A button the UI greys out is a hint; the
refusal is here, and the tests call it directly. The two gates are docs/04's:

- nobody is *screened* without an assessment a person has looked at -- moving them is
  that person's sign-off, and the transition records who gave it;
- nobody reaches *offer* without enough submitted scorecards, so an offer always rests
  on more than one interviewer's view.

Rejection needs a reason code chosen by a person. That is good practice, and it is also
what makes adverse-impact reporting possible later: a free-text field cannot be counted.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from insightlm.recruiting.domain.assessment import Application, Stage
from insightlm.shared.domain.errors import ConflictError, ValidationError
from insightlm.shared.domain.ids import ApplicationId, TenantId, UserId

# The happy path, in order. REJECTED sits outside it: reachable from any open stage.
FORWARD: tuple[Stage, ...] = (
    Stage.APPLIED,
    Stage.SCREENED,
    Stage.INTERVIEW,
    Stage.OFFER,
    Stage.HIRED,
)
TERMINAL = frozenset({Stage.HIRED, Stage.REJECTED})

REQUIRED_SCORECARDS_FOR_OFFER = 2

# How long an application may sit in a stage before it is shown as overdue. Candidates
# notice silence long before recruiters do; making it visible is most of the fix.
STAGE_SLA: dict[Stage, timedelta] = {
    Stage.APPLIED: timedelta(days=3),
    Stage.SCREENED: timedelta(days=5),
    Stage.INTERVIEW: timedelta(days=10),
    Stage.OFFER: timedelta(days=5),
}


class RejectionReason(StrEnum):
    SKILLS_GAP = "skills_gap"
    EXPERIENCE_LEVEL = "experience_level"
    INTERVIEW_PERFORMANCE = "interview_performance"
    COMPENSATION = "compensation"
    LOCATION = "location"
    POSITION_FILLED = "position_filled"
    CANDIDATE_WITHDREW = "candidate_withdrew"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class GateFacts:
    """What the gates need to know, gathered by the use case. Nothing else is consulted."""

    has_assessment: bool
    submitted_scorecards: int


@dataclass(frozen=True, slots=True)
class StageTransition:
    tenant_id: TenantId
    application_id: ApplicationId
    from_stage: Stage
    to_stage: Stage
    by: UserId
    at: datetime
    reason: RejectionReason | None = None
    note: str = ""


def next_stage(stage: Stage) -> Stage | None:
    if stage in TERMINAL:
        return None
    return FORWARD[FORWARD.index(stage) + 1]


def blockers(application: Application, to: Stage, facts: GateFacts) -> list[str]:
    """Every reason the move cannot happen, in words a recruiter can act on.

    Returned rather than raised so the UI can show *why* a move is unavailable before
    anyone tries it. `transition` raises on the same list, so the two cannot disagree.
    """
    current = application.stage
    if current in TERMINAL:
        return [f"the application is already {current.value}"]
    if to is Stage.REJECTED:
        return []
    if to != next_stage(current):
        return [f"{current.value} can only move to {next_stage(current)} or rejected"]

    reasons: list[str] = []
    if to is Stage.SCREENED and not facts.has_assessment:
        reasons.append("assess the candidate before screening them")
    if to is Stage.OFFER and facts.submitted_scorecards < REQUIRED_SCORECARDS_FOR_OFFER:
        missing = REQUIRED_SCORECARDS_FOR_OFFER - facts.submitted_scorecards
        reasons.append(
            f"{missing} more submitted scorecard{'s' if missing > 1 else ''} needed "
            f"before an offer ({facts.submitted_scorecards} of "
            f"{REQUIRED_SCORECARDS_FOR_OFFER})"
        )
    return reasons


def transition(
    application: Application,
    to: Stage,
    *,
    facts: GateFacts,
    by: UserId,
    at: datetime,
    reason: RejectionReason | None = None,
    note: str = "",
) -> tuple[Application, StageTransition]:
    if to is Stage.REJECTED and reason is None:
        raise ValidationError("a rejection needs a reason code")
    if to is not Stage.REJECTED and reason is not None:
        raise ValidationError("a reason code is only recorded on rejection")

    problems = blockers(application, to, facts)
    if problems:
        raise ConflictError("; ".join(problems), detail={"blockers": problems})

    moved = replace(application, stage=to, stage_entered_at=at)
    record = StageTransition(
        tenant_id=application.tenant_id,
        application_id=application.id,
        from_stage=application.stage,
        to_stage=to,
        by=by,
        at=at,
        reason=reason,
        note=note.strip(),
    )
    return moved, record


def is_overdue(application: Application, now: datetime) -> bool:
    sla = STAGE_SLA.get(application.stage)
    return sla is not None and now - application.stage_entered_at > sla
