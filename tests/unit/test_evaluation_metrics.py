"""Metrics, checked against worked examples.

These numbers become the dissertation's results. A metric that is subtly wrong
produces a table that is confidently wrong, and nothing downstream would notice —
so each is pinned to a value computed by hand rather than to whatever the code
happened to return.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from insightlm.evaluation.domain.grounding import (
    AbstentionOutcome,
    score_abstention,
    score_citations,
)
from insightlm.evaluation.domain.metrics import (
    average_precision,
    hit_rate_at_k,
    mean_reciprocal_rank,
    ndcg_at_k,
    precision_at_k,
    recall_at_k,
    score_retrieval,
)

RANKED = ["a", "b", "c", "d", "e"]
RELEVANT = {"a", "c", "x"}  # "x" is relevant but never retrieved


# --------------------------------------------------------------------- recall
def test_recall_counts_only_what_was_found() -> None:
    # 2 of 3 relevant items ({"a", "c"}) appear in the top 5.
    assert recall_at_k(RANKED, RELEVANT, 5) == pytest.approx(2 / 3)


def test_recall_respects_the_cutoff() -> None:
    # Only "a" is within the top 2.
    assert recall_at_k(RANKED, RELEVANT, 2) == pytest.approx(1 / 3)


def test_recall_is_undefined_when_nothing_is_relevant() -> None:
    """Not zero.

    Scoring an item with no relevant passages as 0.0 drags down the average for
    questions that were never answerable — which is precisely the population of
    unanswerable items the golden set deliberately contains.
    """
    assert recall_at_k(RANKED, set(), 5) is None


def test_recall_of_an_empty_retrieval_is_zero_not_undefined() -> None:
    assert recall_at_k([], RELEVANT, 5) == 0.0


# ------------------------------------------------------------------ precision
def test_precision_divides_by_k_not_by_what_was_returned() -> None:
    """Returning 2 results when 5 were asked for is a real cost.

    Normalising by the returned count would score a system that retrieves one perfect
    result the same as one that retrieves five, hiding the unused context budget.
    """
    assert precision_at_k(["a", "c"], RELEVANT, 5) == pytest.approx(2 / 5)
    assert precision_at_k(["a", "c"], RELEVANT, 2) == pytest.approx(1.0)


# ------------------------------------------------------------------------ MRR
def test_mrr_is_the_reciprocal_of_the_first_hit() -> None:
    assert mean_reciprocal_rank(["z", "a", "c"], RELEVANT) == pytest.approx(1 / 2)
    assert mean_reciprocal_rank(["a", "z"], RELEVANT) == pytest.approx(1.0)


def test_mrr_is_zero_when_nothing_relevant_is_retrieved() -> None:
    assert mean_reciprocal_rank(["y", "z"], RELEVANT) == 0.0


# ----------------------------------------------------------------------- nDCG
def test_ndcg_rewards_ranking_relevant_items_earlier() -> None:
    """The metric a reranker is supposed to move.

    Both lists retrieve the same two relevant items, so recall is identical. Only
    nDCG distinguishes them.
    """
    early = ndcg_at_k(["a", "c", "z"], RELEVANT, 3)
    late = ndcg_at_k(["z", "a", "c"], RELEVANT, 3)
    assert early is not None and late is not None
    assert early > late
    assert recall_at_k(["a", "c", "z"], RELEVANT, 3) == recall_at_k(["z", "a", "c"], RELEVANT, 3)


def test_ndcg_matches_a_hand_computation() -> None:
    relevant = {"a", "b"}
    # Ranks 1 and 3 hit: DCG = 1/log2(2) + 1/log2(4) = 1.0 + 0.5
    # Ideal packs both into ranks 1 and 2: 1.0 + 1/log2(3)
    expected = (1.0 + 0.5) / (1.0 + 1.0 / math.log2(3))
    assert ndcg_at_k(["a", "z", "b"], relevant, 3) == pytest.approx(expected)


def test_a_perfect_ranking_scores_one() -> None:
    assert ndcg_at_k(["a", "b", "z"], {"a", "b"}, 3) == pytest.approx(1.0)


# ------------------------------------------------------- average precision / hit
def test_average_precision_matches_a_hand_computation() -> None:
    # Hits at ranks 1 and 3: (1/1 + 2/3) / 2
    assert average_precision(["a", "z", "b"], {"a", "b"}) == pytest.approx((1.0 + 2 / 3) / 2)


def test_hit_rate_is_binary() -> None:
    assert hit_rate_at_k(["z", "a"], RELEVANT, 2) == 1.0
    assert hit_rate_at_k(["z", "y"], RELEVANT, 2) == 0.0


# -------------------------------------------------------------------- bundling
def test_score_retrieval_computes_everything_from_one_list() -> None:
    scores = score_retrieval(RANKED, RELEVANT, k=5)
    assert scores.k == 5
    assert scores.relevant_count == 3
    assert scores.retrieved_count == 5
    assert scores.recall == pytest.approx(2 / 3)
    assert scores.mrr == pytest.approx(1.0)


# ------------------------------------------------------------------- citations
def test_citation_precision_penalises_citing_irrelevant_passages() -> None:
    """The failure that makes a grounded answer feel trustworthy without being so."""
    scores = score_citations(
        cited=["a", "z"],
        relevant={"a", "c"},
        retrieved=["a", "z", "c"],
        claims_total=2,
        claims_uncited=0,
    )
    assert scores.citation_precision == pytest.approx(0.5)
    assert scores.citation_recall == pytest.approx(0.5)
    assert scores.citations_unresolvable == 0


def test_a_citation_naming_an_unretrieved_passage_is_counted_as_unresolvable() -> None:
    """Should be impossible — the validator drops these before display.

    Measured anyway: an invariant nobody checks is an assumption.
    """
    scores = score_citations(
        cited=["a", "ghost"],
        relevant={"a"},
        retrieved=["a", "b"],
        claims_total=1,
        claims_uncited=0,
    )
    assert scores.citations_unresolvable == 1


# ------------------------------------------------------------------ abstention
def test_abstention_separates_the_two_failure_directions() -> None:
    """A system that never refuses and one that always refuses are both useless.

    Averaging them into a single "abstention accuracy" would hide which way a
    configuration is failing, which is the only actionable part.
    """
    rates = score_abstention(
        [
            AbstentionOutcome(answerable=True, abstained=False),  # correct answer
            AbstentionOutcome(answerable=True, abstained=True),  # over-refusal
            AbstentionOutcome(answerable=False, abstained=True),  # correct refusal
            AbstentionOutcome(answerable=False, abstained=False),  # answered anyway
        ]
    )
    assert rates.answerable_items == 2
    assert rates.unanswerable_items == 2
    assert rates.over_refusal_rate == pytest.approx(0.5)
    assert rates.correct_abstention_rate == pytest.approx(0.5)
    assert rates.hallucination_risk_rate == pytest.approx(0.5)


def test_abstention_rates_are_undefined_without_items_of_that_kind() -> None:
    rates = score_abstention([AbstentionOutcome(answerable=True, abstained=False)])
    assert rates.correct_abstention_rate is None
    assert rates.over_refusal_rate == 0.0


# -------------------------------------------------------------------- properties
@given(
    retrieved=st.lists(st.sampled_from("abcdefgh"), max_size=8, unique=True),
    relevant=st.sets(st.sampled_from("abcdefgh"), min_size=1, max_size=5),
    k=st.integers(min_value=1, max_value=8),
)
@settings(max_examples=200, deadline=None)
def test_every_metric_stays_within_zero_and_one(
    retrieved: list[str], relevant: set[str], k: int
) -> None:
    for value in (
        recall_at_k(retrieved, relevant, k),
        precision_at_k(retrieved, relevant, k),
        mean_reciprocal_rank(retrieved, relevant),
        ndcg_at_k(retrieved, relevant, k),
        average_precision(retrieved, relevant),
        hit_rate_at_k(retrieved, relevant, k),
    ):
        assert value is None or 0.0 <= value <= 1.0


@given(
    relevant=st.sets(st.sampled_from("abcd"), min_size=1, max_size=4),
    k=st.integers(min_value=1, max_value=6),
)
@settings(max_examples=100, deadline=None)
def test_recall_never_decreases_as_k_grows(relevant: set[str], k: int) -> None:
    """A monotonicity property the implementation must not violate."""
    retrieved = list("abcdef")
    smaller = recall_at_k(retrieved, relevant, k)
    larger = recall_at_k(retrieved, relevant, k + 1)
    assert smaller is not None and larger is not None
    assert larger >= smaller
