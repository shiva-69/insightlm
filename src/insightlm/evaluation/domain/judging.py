"""Measuring a criterion judge against labelled cases.

The judge answers a narrow question -- does this evidence support this one criterion?
-- so it can be measured the way a classifier is. Each case lists the verdicts a careful
human would accept (strong vs adequate is a judgment call; strong vs absent is not) and
which sources genuinely support the criterion, so a correct verdict citing the wrong
passage still counts as a miss on citations.

Cases are grouped by the failure they probe. The groups matter more than the total:
a baseline that matches words will pass `direct` and fail `paraphrase` and `negation`,
and a judge worth paying for has to do the opposite.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum

from insightlm.recruiting.domain.assessment import Verdict


class CaseKind(StrEnum):
    DIRECT = "direct"  # the passage says it, in the criterion's words
    PARAPHRASE = "paraphrase"  # the passage says it in different words
    MENTION = "mention"  # the topic appears, but only in passing
    NEGATION = "negation"  # the passage uses the words to deny it
    UNRELATED = "unrelated"  # nothing relevant at all


@dataclass(frozen=True, slots=True)
class JudgeCase:
    id: str
    kind: CaseKind
    criterion: str
    passages: tuple[str, ...]  # become C1, C2, ... in order
    acceptable: frozenset[Verdict]
    supporting: frozenset[str] = frozenset()  # labels that genuinely support it
    hint: str = ""

    @property
    def expects_support(self) -> bool | None:
        """None when the labels accept both "absent" and a finding: ambiguous by design."""
        if self.acceptable == {Verdict.ABSENT}:
            return False
        if Verdict.ABSENT not in self.acceptable:
            return True
        return None


@dataclass(frozen=True, slots=True)
class JudgeOutcome:
    case: JudgeCase
    verdict: Verdict
    cited: tuple[str, ...]

    @property
    def correct(self) -> bool:
        return self.verdict in self.case.acceptable

    @property
    def detected_correctly(self) -> bool | None:
        """The binary question: is there support at all? Undefined for ambiguous cases."""
        expected = self.case.expects_support
        if expected is None:
            return None
        return (self.verdict is not Verdict.ABSENT) == expected

    @property
    def citations_correct(self) -> bool | None:
        """Undefined for a verdict that should cite nothing and didn't."""
        if self.verdict is Verdict.ABSENT:
            return None
        return bool(self.cited) and set(self.cited) <= self.case.supporting


@dataclass(slots=True)
class JudgeReport:
    judge: str
    outcomes: list[JudgeOutcome] = field(default_factory=list)

    def _rate(self, values: list[bool]) -> float | None:
        return sum(values) / len(values) if values else None

    @property
    def accuracy(self) -> float | None:
        return self._rate([o.correct for o in self.outcomes])

    @property
    def detection(self) -> float | None:
        graded = [d for o in self.outcomes if (d := o.detected_correctly) is not None]
        return self._rate(graded)

    @property
    def citation_precision(self) -> float | None:
        graded = [c for o in self.outcomes if (c := o.citations_correct) is not None]
        return self._rate(graded)

    def by_kind(self) -> dict[CaseKind, float | None]:
        groups: dict[CaseKind, list[bool]] = defaultdict(list)
        for outcome in self.outcomes:
            groups[outcome.case.kind].append(outcome.correct)
        return {kind: self._rate(groups[kind]) for kind in CaseKind}

    @property
    def misses(self) -> list[JudgeOutcome]:
        return [o for o in self.outcomes if not o.correct]
