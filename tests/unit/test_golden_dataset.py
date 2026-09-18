"""The golden dataset, and why it is labelled by quote.

The resolution tests are the important ones. If labels do not survive re-chunking,
every ablation comparison is against a different ground truth and the results table
is meaningless.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from insightlm.evaluation.adapters.golden import SEED_DATASET
from insightlm.evaluation.domain.dataset import (
    GoldenDataset,
    GoldenItem,
    ItemKind,
    normalise,
)
from insightlm.shared.domain.errors import ValidationError

CHUNKS = [
    (
        "c1",
        "EXPERIENCE\nLed the settlement platform at a payments company, owning\nreconciliation and idempotent retries.",
    ),
    ("c2", "SKILLS\nPython, PostgreSQL, pgvector\nKubernetes, Docker, Redis"),
    ("c3", "EDUCATION\nMCA in progress, expected 2027."),
]


def test_quotes_resolve_across_the_line_breaks_pdf_extraction_introduces() -> None:
    """The quote spans a newline in the chunk. Literal matching would miss it."""
    dataset = GoldenDataset.of(
        "t",
        [
            GoldenItem(
                id="a",
                question="q",
                kind=ItemKind.FACTUAL,
                quotes=(
                    "Led the settlement platform at a payments company, owning reconciliation",
                ),
            ),
            GoldenItem(id="u", question="q2", kind=ItemKind.UNANSWERABLE),
        ],
    )
    resolved = dataset.resolve(CHUNKS)
    assert resolved[0].relevant_chunk_ids == {"c1"}
    assert resolved[0].unmatched_quotes == ()


def test_labels_survive_a_change_of_chunk_size() -> None:
    """The property the whole harness depends on.

    Chunking is an ablation arm, so the ground truth has to hold when it varies. The
    same quote must resolve whether the corpus was split into three chunks or one.
    """
    item = GoldenItem(
        id="a",
        question="q",
        kind=ItemKind.FACTUAL,
        quotes=("Kubernetes, Docker, Redis",),
    )
    dataset = GoldenDataset.of(
        "t", [item, GoldenItem(id="u", question="q2", kind=ItemKind.UNANSWERABLE)]
    )

    fine = dataset.resolve(CHUNKS)[0]
    coarse = dataset.resolve([("big", "\n".join(text for _, text in CHUNKS))])[0]

    assert fine.relevant_chunk_ids == {"c2"}
    assert coarse.relevant_chunk_ids == {"big"}
    assert fine.unmatched_quotes == coarse.unmatched_quotes == ()


def test_an_unmatched_quote_is_reported_rather_than_silently_dropped() -> None:
    """A quote that matches nothing makes an item look unanswerable.

    Recall would then be understated with no error anywhere, which is the kind of
    quiet wrongness that survives into a results table.
    """
    dataset = GoldenDataset.of(
        "t",
        [
            GoldenItem(
                id="a", question="q", kind=ItemKind.FACTUAL, quotes=("nowhere in the corpus",)
            ),
            GoldenItem(id="u", question="q2", kind=ItemKind.UNANSWERABLE),
        ],
    )
    resolved = dataset.resolve(CHUNKS)[0]
    assert resolved.relevant_chunk_ids == frozenset()
    assert resolved.unmatched_quotes == ("nowhere in the corpus",)


def test_normalisation_is_case_and_whitespace_insensitive() -> None:
    assert normalise("  Led   the\nsettlement ") == "led the settlement"


# ------------------------------------------------------------------ invariants
def test_an_answerable_item_must_name_its_evidence() -> None:
    with pytest.raises(ValidationError, match="names no supporting quote"):
        GoldenItem(id="a", question="q", kind=ItemKind.FACTUAL)


def test_an_unanswerable_item_must_not_name_evidence() -> None:
    """A contradiction in the label, and it would silently corrupt abstention scoring."""
    with pytest.raises(ValidationError, match="unanswerable but names supporting quotes"):
        GoldenItem(id="a", question="q", kind=ItemKind.UNANSWERABLE, quotes=("x",))


def test_a_dataset_without_unanswerable_items_is_rejected() -> None:
    """Without them a system that never refuses scores perfectly."""
    with pytest.raises(ValidationError, match="no unanswerable items"):
        GoldenDataset.of(
            "t",
            [GoldenItem(id="a", question="q", kind=ItemKind.FACTUAL, quotes=("x",))],
        )


def test_duplicate_ids_are_rejected() -> None:
    with pytest.raises(ValidationError, match="duplicate item id"):
        GoldenDataset.of(
            "t",
            [
                GoldenItem(id="a", question="q", kind=ItemKind.FACTUAL, quotes=("x",)),
                GoldenItem(id="a", question="q2", kind=ItemKind.UNANSWERABLE),
            ],
        )


# ----------------------------------------------------------------- the seed set
def test_the_seed_set_is_valid_and_balanced() -> None:
    SEED_DATASET.validate()
    unanswerable = len(SEED_DATASET.of_kind(ItemKind.UNANSWERABLE))
    share = unanswerable / len(SEED_DATASET.items)
    # docs/03 section 5 calls for roughly 15-20%; the seed set runs a little higher
    # because it is small and the near-miss items are the interesting ones.
    assert 0.15 <= share <= 0.35


def test_every_seed_item_records_why_it_exists() -> None:
    """An item whose purpose nobody can state is one that gets silently broken."""
    for item in SEED_DATASET.items:
        assert item.notes.strip(), f"{item.id} has no rationale"


def test_the_seed_set_covers_every_answerable_kind() -> None:
    for kind in (ItemKind.FACTUAL, ItemKind.MULTI_HOP, ItemKind.COMPARATIVE):
        assert SEED_DATASET.of_kind(kind), f"no {kind.value} items"


def test_the_checked_in_dataset_matches_the_code(tmp_path: Path) -> None:
    """`eval/datasets/seed-v1.json` is generated from `golden.py`; it must not drift."""
    checked_in = Path("eval/datasets/seed-v1.json")
    if not checked_in.is_file():
        pytest.skip("dataset not exported yet")

    regenerated = tmp_path / "seed.json"
    SEED_DATASET.save(regenerated)
    assert json.loads(checked_in.read_text()) == json.loads(regenerated.read_text())
