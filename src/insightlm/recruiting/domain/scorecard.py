"""Interview scorecards, and the rule that stops the first opinion becoming everyone's.

**Anchoring protection.** An interviewer on the panel sees nobody else's ratings until
they have submitted their own. Once someone reads "strong yes" from the hiring manager,
their own write-up bends toward it. That's well documented in structured-interview
research, and it's cheap to prevent: the ratings are withheld, not merely hidden in
the UI.

A scorecard exists from the moment someone is put on the panel (an *assignment*) and is
a draft until submitted. A submitted scorecard is final. It can't be edited, only
followed by a note elsewhere. Otherwise the protection could be undone by submitting,
reading everyone else's, and revising.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from insightlm.recruiting.domain.rubric import Rubric
from insightlm.shared.domain.errors import ConflictError, ValidationError
from insightlm.shared.domain.ids import (
    ApplicationId,
    CriterionId,
    ScorecardId,
    TenantId,
    UserId,
)


class Recommendation(StrEnum):
    """Four options and no "maybe": a scorecard exists to make someone commit."""

    STRONG_NO = "strong_no"
    NO = "no"
    YES = "yes"
    STRONG_YES = "strong_yes"


@dataclass(frozen=True, slots=True)
class CriterionRating:
    criterion_id: CriterionId
    rating: int  # 1 (concern) .. 4 (strong)
    note: str = ""

    def __post_init__(self) -> None:
        if not 1 <= self.rating <= 4:
            raise ValidationError("ratings run from 1 to 4")


@dataclass(frozen=True, slots=True)
class Scorecard:
    id: ScorecardId
    tenant_id: TenantId
    application_id: ApplicationId
    interviewer_id: UserId
    assigned_by: UserId
    assigned_at: datetime
    ratings: tuple[CriterionRating, ...] = ()
    recommendation: Recommendation | None = None
    summary: str = ""
    submitted_at: datetime | None = None

    @property
    def is_submitted(self) -> bool:
        return self.submitted_at is not None

    def submit(
        self,
        *,
        by: UserId,
        rubric: Rubric,
        ratings: list[CriterionRating],
        recommendation: Recommendation,
        summary: str,
        at: datetime,
    ) -> Scorecard:
        if by != self.interviewer_id:
            raise ConflictError("only the assigned interviewer can submit this scorecard")
        if self.is_submitted:
            raise ConflictError("a submitted scorecard is final")

        known = {c.id for c in rubric.criteria}
        rated = [r.criterion_id for r in ratings]
        if len(set(rated)) != len(rated):
            raise ValidationError("each criterion can be rated once")
        if unknown := [str(c) for c in rated if c not in known]:
            raise ValidationError(
                "ratings name criteria outside the rubric", detail={"ids": unknown}
            )
        if not summary.strip():
            raise ValidationError("say in a sentence why you recommend what you do")

        return replace(
            self,
            ratings=tuple(ratings),
            recommendation=recommendation,
            summary=summary.strip(),
            submitted_at=at,
        )


@dataclass(frozen=True, slots=True)
class ScorecardView:
    """What one viewer may see of an application's scorecards."""

    own: Scorecard | None
    others: tuple[Scorecard, ...]
    withheld: int  # submitted cards the viewer may not read yet
    pending: int  # panel members who have not submitted


def visible_to(viewer: UserId, *, can_view_scores: bool, cards: list[Scorecard]) -> ScorecardView:
    """The anchoring rule, in one place.

    - Someone on the panel who hasn't submitted sees only their own card, plus a count of
      how many others are in, so they know the panel is moving without learning its view.
    - Once they've submitted, or if they aren't on the panel but may view scores, they
      see every *submitted* card.
    - Nobody ever sees another person's draft.
    """
    own = next((c for c in cards if c.interviewer_id == viewer), None)
    submitted_others = tuple(c for c in cards if c.is_submitted and c.interviewer_id != viewer)
    pending = sum(1 for c in cards if not c.is_submitted)

    blind = own is not None and not own.is_submitted
    if blind or (own is None and not can_view_scores):
        return ScorecardView(own=own, others=(), withheld=len(submitted_others), pending=pending)
    return ScorecardView(own=own, others=submitted_others, withheld=0, pending=pending)
