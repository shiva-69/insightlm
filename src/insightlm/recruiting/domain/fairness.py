"""Selection rates by group, and the four-fifths rule.

Group membership is never inferred -- not from a name, a photo or a college. It comes
only from voluntary self-identification, which is stored apart from the application,
readable only by the owner and auditors, never shown to reviewers and never sent to a
model. A candidate who prefers not to say is counted as undisclosed, not guessed at.

The four-fifths rule (US EEOC Uniform Guidelines, 1978) is a screening heuristic: if a
group's selection rate is below 80% of the most-selected group's, that is evidence of
adverse impact worth investigating. It is not a verdict, and on small numbers it is
noise -- so below a minimum group size the report says "too few to tell" instead of
printing a ratio that looks precise and isn't.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.domain.ids import CandidateId, TenantId

FOUR_FIFTHS = 0.8
MIN_GROUP_SIZE = 5
UNDISCLOSED = "undisclosed"


class Dimension(StrEnum):
    GENDER = "gender"


ALLOWED: dict[Dimension, frozenset[str]] = {
    Dimension.GENDER: frozenset({"woman", "man", "non_binary", "prefer_not_to_say"}),
}


@dataclass(frozen=True, slots=True)
class SelfIdentification:
    tenant_id: TenantId
    candidate_id: CandidateId
    dimension: Dimension
    value: str
    recorded_at: datetime

    def __post_init__(self) -> None:
        if self.value not in ALLOWED[self.dimension]:
            raise ValidationError(
                f"{self.value!r} is not an option for {self.dimension.value}",
                detail={"options": sorted(ALLOWED[self.dimension])},
            )

    @property
    def group(self) -> str:
        return UNDISCLOSED if self.value == "prefer_not_to_say" else self.value


@dataclass(frozen=True, slots=True)
class Outcome:
    """One screening decision: did the application get past the first human gate?"""

    group: str
    advanced: bool


@dataclass(frozen=True, slots=True)
class GroupRate:
    group: str
    decided: int
    advanced: int
    rate: float | None
    impact_ratio: float | None  # rate / highest disclosed group's rate
    adverse: bool
    too_few: bool


def selection_rates(outcomes: list[Outcome]) -> list[GroupRate]:
    counts: dict[str, list[int]] = defaultdict(lambda: [0, 0])  # group -> [decided, advanced]
    for outcome in outcomes:
        counts[outcome.group][0] += 1
        counts[outcome.group][1] += int(outcome.advanced)

    rates = {g: (a / d if d else None) for g, (d, a) in counts.items()}
    comparable = [
        r
        for g, r in rates.items()
        if r is not None and g != UNDISCLOSED and counts[g][0] >= MIN_GROUP_SIZE
    ]
    best = max(comparable) if comparable else None

    report: list[GroupRate] = []
    for group, (decided, advanced) in sorted(counts.items()):
        rate = rates[group]
        too_few = decided < MIN_GROUP_SIZE
        ratio = (
            rate / best
            if rate is not None and best and not too_few and group != UNDISCLOSED
            else None
        )
        report.append(
            GroupRate(
                group=group,
                decided=decided,
                advanced=advanced,
                rate=rate,
                impact_ratio=ratio,
                adverse=ratio is not None and ratio < FOUR_FIFTHS,
                too_few=too_few,
            )
        )
    return report
