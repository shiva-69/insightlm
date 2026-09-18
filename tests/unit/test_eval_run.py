"""Aggregating a run.

The averaging rule is the thing to get right: undefined metrics are excluded, not
zero-filled. Zero-filling makes every configuration look worse in proportion to how
many unanswerable items the dataset happens to contain, which would make the results
table a function of the dataset's composition rather than of the pipeline.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from insightlm.evaluation.domain.dataset import ItemKind
from insightlm.evaluation.domain.grounding import score_citations
from insightlm.evaluation.domain.metrics import score_retrieval
from insightlm.evaluation.domain.run import EvalConfig, EvalRun, ItemOutcome

CONFIG = EvalConfig(name="test", strategy="dense", top_k=5, generator="fake", embedder="fake")


def outcome(
    item_id: str,
    kind: ItemKind,
    *,
    retrieved: list[str] | None = None,
    relevant: set[str] | None = None,
    abstained: bool = False,
    cited: list[str] | None = None,
) -> ItemOutcome:
    answerable = kind is not ItemKind.UNANSWERABLE
    scores = (
        score_retrieval(retrieved or [], relevant or set(), k=5) if retrieved is not None else None
    )
    citations = (
        score_citations(
            cited=cited or [],
            relevant=relevant or set(),
            retrieved=retrieved or [],
            claims_total=len(cited or []),
            claims_uncited=0,
        )
        if cited is not None
        else None
    )
    return ItemOutcome(
        item_id=item_id,
        kind=kind,
        answerable=answerable,
        abstained=abstained,
        retrieval=scores,
        citations=citations,
        latency_ms=100,
    )


def run_of(*outcomes: ItemOutcome) -> EvalRun:
    return EvalRun(
        config=CONFIG,
        dataset_name="test",
        started_at=datetime.now(UTC),
        outcomes=outcomes,
    )


def test_unanswerable_items_do_not_deflate_retrieval_averages() -> None:
    """The averaging rule, demonstrated.

    Both runs contain the same one answerable item scored perfectly. Adding
    unanswerable items -- whose recall is undefined -- must not move the number.
    """
    without = run_of(outcome("a", ItemKind.FACTUAL, retrieved=["x"], relevant={"x"}))
    with_unanswerable = run_of(
        outcome("a", ItemKind.FACTUAL, retrieved=["x"], relevant={"x"}),
        outcome("u1", ItemKind.UNANSWERABLE, retrieved=["y"], relevant=set(), abstained=True),
        outcome("u2", ItemKind.UNANSWERABLE, retrieved=["z"], relevant=set(), abstained=True),
    )

    assert without.summary().recall_at_k == pytest.approx(1.0)
    assert with_unanswerable.summary().recall_at_k == pytest.approx(1.0)
    assert with_unanswerable.summary().items == 3


def test_the_breakdown_can_disagree_with_the_headline() -> None:
    """Where the interesting findings live.

    A configuration can hold a strong average while being weak on one kind. Reporting
    only the headline hides exactly the part worth acting on.
    """
    run = run_of(
        outcome("f1", ItemKind.FACTUAL, retrieved=["x"], relevant={"x"}),
        outcome("f2", ItemKind.FACTUAL, retrieved=["x"], relevant={"x"}),
        outcome("m1", ItemKind.MULTI_HOP, retrieved=["q"], relevant={"x", "y"}),
    )
    headline = run.summary()
    breakdown = run.breakdown()

    assert headline.recall_at_k == pytest.approx(2 / 3)
    assert breakdown[ItemKind.FACTUAL].recall_at_k == pytest.approx(1.0)
    assert breakdown[ItemKind.MULTI_HOP].recall_at_k == pytest.approx(0.0)


def test_a_system_that_always_refuses_is_not_rewarded() -> None:
    """Why correct-abstention and over-refusal are reported separately.

    Collapsing them into one "abstention accuracy" would score this configuration
    highly, when in fact it answers nothing at all.
    """
    run = run_of(
        outcome("f1", ItemKind.FACTUAL, retrieved=["x"], relevant={"x"}, abstained=True),
        outcome("f2", ItemKind.FACTUAL, retrieved=["x"], relevant={"x"}, abstained=True),
        outcome("u1", ItemKind.UNANSWERABLE, abstained=True),
    )
    rates = run.summary().abstention

    assert rates.correct_abstention_rate == pytest.approx(1.0)  # looks perfect
    assert rates.over_refusal_rate == pytest.approx(1.0)  # and is useless


def test_the_contract_flag_reports_a_violation() -> None:
    clean = run_of(outcome("a", ItemKind.FACTUAL, retrieved=["x"], relevant={"x"}, cited=["x"]))
    assert clean.summary().contract_held

    broken = run_of(
        outcome("a", ItemKind.FACTUAL, retrieved=["x"], relevant={"x"}, cited=["ghost"])
    )
    assert not broken.summary().contract_held
    assert broken.summary().unresolvable_citations == 1


def test_errors_are_counted_and_do_not_abort_aggregation() -> None:
    run = run_of(
        outcome("a", ItemKind.FACTUAL, retrieved=["x"], relevant={"x"}),
        ItemOutcome(
            item_id="b",
            kind=ItemKind.FACTUAL,
            answerable=True,
            abstained=False,
            error="RuntimeError: provider timed out",
        ),
    )
    summary = run.summary()
    assert summary.errors == 1
    assert summary.recall_at_k == pytest.approx(1.0)


def test_an_empty_run_summarises_without_raising() -> None:
    summary = run_of().summary()
    assert summary.items == 0
    assert summary.recall_at_k is None
    assert summary.median_latency_ms is None


def test_the_config_fingerprint_captures_what_changes_the_numbers() -> None:
    """Everything that moves the result has to appear in the fingerprint.

    Contextual enrichment changes what gets embedded, so two runs that differ only in
    it are not comparable -- and a fingerprint that hid the difference would let them
    be filed as the same configuration.
    """
    assert CONFIG.fingerprint() == "dense+ctx/k=5/fake/fake"

    from dataclasses import replace

    without = replace(CONFIG, enrich_with_title=False)
    assert without.fingerprint() == "dense-ctx/k=5/fake/fake"
    assert without.fingerprint() != CONFIG.fingerprint()
