"""Reciprocal Rank Fusion.

Two retrieval arms return two ranked lists with scores on incomparable scales: a
cosine similarity around 0.7 and a `ts_rank_cd` around 0.06 mean nothing to each
other. Normalising them into a common range requires knowing each arm's score
distribution, which changes with the corpus, the query and the embedding model --
so any fixed normalisation is a tuning parameter pretending to be arithmetic.

RRF sidesteps it by discarding the scores and fusing on *rank* alone:

    score(d) = sum over arms of 1 / (k + rank(d))

A document ranked highly by either arm scores well; one ranked highly by both scores
best. The constant `k` damps the influence of the very top ranks, so a single arm
placing something first cannot by itself dominate the fusion. 60 is the value from
the original paper and is what almost everyone uses.

The property that matters for this project: lexical retrieval finds exact terms --
`PCI-DSS`, `CKA`, a framework version -- that dense retrieval fumbles, and dense
retrieval finds paraphrase that lexical search cannot see. Fusion keeps both without
having to decide in advance which one a given query needs.

Pure and dependency-free, so the behaviour is unit-testable without a database.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

DEFAULT_RRF_K = 60


@dataclass(frozen=True, slots=True)
class FusedHit:
    key: str
    score: float
    contributing_arms: tuple[str, ...]
    """Which arms found this. Kept because "the lexical arm was the only one that
    found the right passage" is the observation that justifies running two arms, and
    it is invisible once the lists are merged."""

    best_rank: int


def reciprocal_rank_fusion(
    rankings: dict[str, Sequence[str]], *, k: int = DEFAULT_RRF_K, limit: int | None = None
) -> list[FusedHit]:
    """Fuse ranked lists into one. ``rankings`` maps an arm name to its ranked keys.

    Ranks are 1-based. Ties break on the best single-arm rank, then on the key, so
    the output is deterministic -- an evaluation harness that reorders ties between
    runs reports differences that are not there.
    """
    scores: dict[str, float] = {}
    arms: dict[str, list[str]] = {}
    best_rank: dict[str, int] = {}

    for arm, ranked in rankings.items():
        for position, key in enumerate(ranked, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (k + position)
            arms.setdefault(key, []).append(arm)
            best_rank[key] = min(best_rank.get(key, position), position)

    fused = [
        FusedHit(
            key=key,
            score=score,
            contributing_arms=tuple(arms[key]),
            best_rank=best_rank[key],
        )
        for key, score in scores.items()
    ]
    fused.sort(key=lambda hit: (-hit.score, hit.best_rank, hit.key))
    return fused[:limit] if limit is not None else fused
