"""Retrieval and generation metrics.

Pure functions over ids and labels: no I/O, no model calls, no framework. That is
deliberate. These numbers become the results chapter, so they have to be inspectable,
unit-testable against worked examples, and identical across runs.

Every function here documents what it measures and — more importantly — what it does
when the question is ill-posed. An empty relevant set makes recall undefined, not
zero; conflating the two silently inflates an average over items that had nothing to
find. Undefined is returned as ``None`` and excluded from aggregation.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass

__all__ = [
    "RetrievalScores",
    "average_precision",
    "hit_rate_at_k",
    "mean_reciprocal_rank",
    "ndcg_at_k",
    "precision_at_k",
    "recall_at_k",
    "score_retrieval",
]


def recall_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float | None:
    """Fraction of the relevant items that appear in the top k.

    The headline retrieval number: a generator cannot cite what retrieval never
    returned, so recall is the ceiling on everything downstream.

    Returns ``None`` when nothing is relevant — recall over an empty target set is
    undefined, and scoring it as 0.0 would drag down an average for items that were
    never answerable in the first place.
    """
    if not relevant:
        return None
    found = len(set(retrieved[:k]) & relevant)
    return found / len(relevant)


def precision_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float | None:
    """Fraction of the top k that is relevant.

    Divides by ``k`` rather than by the number actually retrieved. Returning fewer
    results than asked for is a real cost — it leaves context-window budget unused —
    and normalising it away would hide that.
    """
    if k <= 0:
        return None
    return len(set(retrieved[:k]) & relevant) / k


def mean_reciprocal_rank(retrieved: Sequence[str], relevant: set[str]) -> float | None:
    """1 / rank of the first relevant item; 0.0 if none is found.

    Sensitive only to the first hit, which is the right shape for "did the top of the
    list contain the answer" and the wrong shape for "did it find everything". Always
    reported alongside recall for that reason.
    """
    if not relevant:
        return None
    for index, item in enumerate(retrieved, start=1):
        if item in relevant:
            return 1.0 / index
    return 0.0


def ndcg_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float | None:
    """Normalised discounted cumulative gain, binary relevance.

    Unlike recall, this rewards putting relevant items *early*. Two configurations
    can retrieve the same set and differ here, which is exactly what a reranker is
    supposed to change — so this is the metric that shows whether Phase 3's reranking
    earns its cost.
    """
    if not relevant:
        return None

    gains = sum(
        1.0 / math.log2(rank + 1)
        for rank, item in enumerate(retrieved[:k], start=1)
        if item in relevant
    )
    # Ideal ranking: every relevant item packed into the top positions, capped at k.
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), k) + 1))
    return gains / ideal if ideal > 0 else None


def average_precision(retrieved: Sequence[str], relevant: set[str]) -> float | None:
    """Precision averaged at each position where a relevant item is found.

    Rewards finding relevant items early *and* finding all of them, which is why it
    is reported next to MRR rather than instead of it.
    """
    if not relevant:
        return None

    hits = 0
    total = 0.0
    for index, item in enumerate(retrieved, start=1):
        if item in relevant:
            hits += 1
            total += hits / index
    return total / len(relevant)


def hit_rate_at_k(retrieved: Sequence[str], relevant: set[str], k: int) -> float | None:
    """Whether *any* relevant item is in the top k.

    Coarse, but it answers the question a user actually has — "did it find anything
    useful" — and it moves when recall does not.
    """
    if not relevant:
        return None
    return 1.0 if set(retrieved[:k]) & relevant else 0.0


@dataclass(frozen=True, slots=True)
class RetrievalScores:
    """Every retrieval metric for one item, at one k."""

    k: int
    recall: float | None
    precision: float | None
    mrr: float | None
    ndcg: float | None
    average_precision: float | None
    hit_rate: float | None
    retrieved_count: int
    relevant_count: int


def score_retrieval(
    retrieved: Sequence[str], relevant: set[str], *, k: int = 10
) -> RetrievalScores:
    """Score one retrieval, computing every metric from the same ranked list."""
    return RetrievalScores(
        k=k,
        recall=recall_at_k(retrieved, relevant, k),
        precision=precision_at_k(retrieved, relevant, k),
        mrr=mean_reciprocal_rank(retrieved, relevant),
        ndcg=ndcg_at_k(retrieved, relevant, k),
        average_precision=average_precision(retrieved, relevant),
        hit_rate=hit_rate_at_k(retrieved, relevant, k),
        retrieved_count=len(retrieved),
        relevant_count=len(relevant),
    )
