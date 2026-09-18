"""Metrics for the citation contract and for abstention.

These are the numbers that speak to RQ1 — whether enforcing citations reduces
unsupported claims — and they are computed from the system's own output rather than
from a judge model, so they are exact rather than estimated.

Faithfulness proper (does the cited passage actually *support* the claim?) needs a
judge and lives behind a port. What is here is the mechanical half: were the citations
real, did they point at relevant passages, and did the system decline when it should
have.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CitationScores:
    citation_precision: float | None
    """Of the passages cited, how many were actually relevant.

    Low precision means the answer is padded with citations that look supportive and
    are not — the failure mode that makes a grounded answer *feel* trustworthy while
    being no better than an ungrounded one."""

    citation_recall: float | None
    """Of the relevant passages, how many were cited."""

    claims_total: int
    claims_uncited: int
    """Should always be zero: the domain refuses to construct an uncited claim. It is
    measured anyway, because an invariant nobody checks is an assumption."""

    citations_total: int
    citations_unresolvable: int
    """Citations naming a passage that was never retrieved -- a confabulated
    reference. Should also always be zero, for the same reason."""


def score_citations(
    *,
    cited: Sequence[str],
    relevant: set[str],
    retrieved: Sequence[str],
    claims_total: int,
    claims_uncited: int,
) -> CitationScores:
    cited_set = set(cited)
    retrieved_set = set(retrieved)

    return CitationScores(
        citation_precision=(len(cited_set & relevant) / len(cited_set) if cited_set else None),
        citation_recall=(len(cited_set & relevant) / len(relevant) if relevant else None),
        claims_total=claims_total,
        claims_uncited=claims_uncited,
        citations_total=len(cited_set),
        citations_unresolvable=len(cited_set - retrieved_set),
    )


@dataclass(frozen=True, slots=True)
class AbstentionOutcome:
    """How the system behaved on one item, judged against whether it was answerable.

    Refusal is a first-class outcome in this system, so it needs first-class
    measurement. A model that never refuses scores perfectly on answerable items and
    is worthless; one that always refuses is trivially safe and equally worthless.
    Both failure directions are counted separately.
    """

    answerable: bool
    abstained: bool

    @property
    def correct_abstention(self) -> bool:
        """Declined an unanswerable question. The behaviour we want."""
        return not self.answerable and self.abstained

    @property
    def over_refusal(self) -> bool:
        """Declined a question the corpus could answer. Caution costing usefulness."""
        return self.answerable and self.abstained

    @property
    def hallucination_risk(self) -> bool:
        """Answered a question the corpus cannot support.

        Named for the risk rather than the fact: the answer may be harmless, but it
        was produced without grounds, which is the condition under which fabrication
        happens.
        """
        return not self.answerable and not self.abstained


@dataclass(frozen=True, slots=True)
class AbstentionRates:
    answerable_items: int
    unanswerable_items: int
    correct_abstention_rate: float | None
    over_refusal_rate: float | None
    hallucination_risk_rate: float | None


def score_abstention(outcomes: Sequence[AbstentionOutcome]) -> AbstentionRates:
    answerable = [o for o in outcomes if o.answerable]
    unanswerable = [o for o in outcomes if not o.answerable]

    return AbstentionRates(
        answerable_items=len(answerable),
        unanswerable_items=len(unanswerable),
        correct_abstention_rate=(
            sum(o.correct_abstention for o in unanswerable) / len(unanswerable)
            if unanswerable
            else None
        ),
        over_refusal_rate=(
            sum(o.over_refusal for o in answerable) / len(answerable) if answerable else None
        ),
        hallucination_risk_rate=(
            sum(o.hallucination_risk for o in unanswerable) / len(unanswerable)
            if unanswerable
            else None
        ),
    )
