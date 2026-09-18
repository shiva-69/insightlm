"""Reciprocal Rank Fusion."""

from __future__ import annotations

import pytest

from insightlm.retrieval.domain.fusion import reciprocal_rank_fusion


def test_a_document_found_by_both_arms_outranks_one_found_by_either() -> None:
    """The property that justifies running two arms at all."""
    fused = reciprocal_rank_fusion(
        {"dense": ["a", "b", "c"], "lexical": ["c", "a", "d"]}
    )
    order = [hit.key for hit in fused]
    assert order[0] == "a"  # rank 1 and rank 2
    assert order.index("a") < order.index("b")
    assert order.index("c") < order.index("d")


def test_fusion_ignores_score_scale_entirely() -> None:
    """Cosine ~0.7 and ts_rank_cd ~0.06 are incomparable.

    RRF never sees them, which is precisely why it needs no normalisation constant
    tuned per corpus and per model.
    """
    fused = reciprocal_rank_fusion({"dense": ["x", "y"], "lexical": ["y", "x"]})
    assert {hit.key for hit in fused} == {"x", "y"}
    # Symmetric input, so the two scores must be equal.
    assert fused[0].score == pytest.approx(fused[1].score)


def test_an_exact_term_match_found_only_lexically_still_surfaces() -> None:
    """The case Phase 3 exists for: `PCI-DSS` that dense retrieval fumbles."""
    fused = reciprocal_rank_fusion(
        {"dense": ["noise1", "noise2", "noise3"], "lexical": ["pcidss"]},
        limit=2,
    )
    assert "pcidss" in [hit.key for hit in fused]


def test_contributing_arms_are_recorded() -> None:
    fused = reciprocal_rank_fusion({"dense": ["a"], "lexical": ["a", "b"]})
    by_key = {hit.key: hit for hit in fused}
    assert set(by_key["a"].contributing_arms) == {"dense", "lexical"}
    assert by_key["b"].contributing_arms == ("lexical",)


def test_a_larger_k_flattens_the_influence_of_top_ranks() -> None:
    """What the constant is for: stopping one arm's first place dominating."""
    rankings = {"dense": ["a", "b"], "lexical": ["b", "a"]}
    tight = reciprocal_rank_fusion(rankings, k=1)
    loose = reciprocal_rank_fusion(rankings, k=1000)
    spread = lambda hits: max(h.score for h in hits) - min(h.score for h in hits)  # noqa: E731
    assert spread(loose) <= spread(tight)


def test_output_is_deterministic_under_ties() -> None:
    """A harness that reorders ties reports differences that are not there."""
    rankings = {"a": ["p", "q", "r"], "b": ["r", "q", "p"]}
    assert [h.key for h in reciprocal_rank_fusion(rankings)] == [
        h.key for h in reciprocal_rank_fusion(rankings)
    ]


def test_a_single_arm_passes_through_in_order() -> None:
    fused = reciprocal_rank_fusion({"dense": ["a", "b", "c"]})
    assert [hit.key for hit in fused] == ["a", "b", "c"]


def test_empty_input_yields_nothing() -> None:
    assert reciprocal_rank_fusion({}) == []
    assert reciprocal_rank_fusion({"dense": []}) == []
