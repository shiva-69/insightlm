"""The golden dataset.

**Items are labelled by quote, not by chunk id.** This is the decision the whole
harness rests on.

A chunk id is assigned at ingestion and changes on every re-ingest. Worse, it changes
whenever the *chunking strategy* changes — and chunking strategy is an arm of the
ablation. Labelling by chunk id would mean the ground truth silently invalidates
itself the moment you vary the thing you are trying to measure, producing a table of
numbers that compare configurations against different ground truths.

So an item records the *text* that must be retrieved. At run time each quote is
resolved against whatever chunks the configuration under test produced, by substring
match on normalised whitespace. The labels then survive re-chunking, re-embedding and
re-ingestion, and one dataset scores every configuration fairly.

The cost is that a quote must be distinctive enough to match exactly one passage.
:meth:`GoldenDataset.validate` enforces that when the dataset is loaded.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from insightlm.shared.domain.errors import ValidationError


class ItemKind(StrEnum):
    FACTUAL = "factual"
    """Answerable from a single passage. The easy case, and the majority."""

    MULTI_HOP = "multi_hop"
    """Needs two or more passages combined. Where naive top-k retrieval starts to
    fail and where query decomposition should earn its cost."""

    COMPARATIVE = "comparative"
    """Requires reading across documents. The extractive baseline cannot answer these
    at all, which is what makes them worth separating out."""

    UNANSWERABLE = "unanswerable"
    """The corpus does not support an answer. Refusing is correct; answering is the
    failure. Roughly a fifth of the set, per docs/03 SS5."""

    ADVERSARIAL = "adversarial"
    """Contains an injection attempt. Scored in Phase 6; carried here so the
    vocabulary is designed rather than bolted on."""


def normalise(text: str) -> str:
    """Collapse whitespace for matching.

    PDF extraction breaks lines wherever the original wrapped, so a quote taken from
    the source will not match the extracted text literally. Normalising both sides is
    what makes quote-based labelling work at all.
    """
    return re.sub(r"\s+", " ", text).strip().lower()


@dataclass(frozen=True, slots=True)
class GoldenItem:
    id: str
    question: str
    kind: ItemKind
    quotes: tuple[str, ...] = ()
    """Passages that must be retrieved for this item to be answerable.

    Empty for unanswerable items, and defaulted so those items read as what they are
    rather than carrying an explicit empty tuple."""
    expected_answer: str | None = None
    """What a good answer contains. Used by the judge, never by the retrieval
    metrics — those compare ids, not prose."""
    documents: tuple[str, ...] = ()
    """Slugs of the documents the quotes come from. Narrows the search when an item
    is scoped; informational otherwise."""
    notes: str = ""
    """Why this item is in the set. Reviewer-facing, and the thing that makes a
    hand-curated dataset auditable months later."""

    @property
    def answerable(self) -> bool:
        return self.kind is not ItemKind.UNANSWERABLE

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValidationError(f"item {self.id} has no question")
        if self.answerable and not self.quotes:
            raise ValidationError(f"item {self.id} is answerable but names no supporting quote")
        if not self.answerable and self.quotes:
            raise ValidationError(f"item {self.id} is unanswerable but names supporting quotes")


@dataclass(frozen=True, slots=True)
class ResolvedItem:
    """A golden item with its quotes bound to the chunks of one specific index."""

    item: GoldenItem
    relevant_chunk_ids: frozenset[str]
    unmatched_quotes: tuple[str, ...] = ()
    """Quotes that matched no chunk. Always reported: a silently unmatched quote
    makes an item look unanswerable and quietly deflates recall."""


@dataclass(frozen=True, slots=True)
class GoldenDataset:
    name: str
    items: tuple[GoldenItem, ...]
    description: str = ""
    corpus: tuple[str, ...] = field(default=())
    """Document slugs the set is written against."""

    def of_kind(self, kind: ItemKind) -> tuple[GoldenItem, ...]:
        return tuple(item for item in self.items if item.kind is kind)

    @property
    def answerable(self) -> tuple[GoldenItem, ...]:
        return tuple(item for item in self.items if item.answerable)

    def validate(self) -> None:
        """Structural checks that must hold before a run is worth starting."""
        seen: set[str] = set()
        for item in self.items:
            if item.id in seen:
                raise ValidationError(f"duplicate item id: {item.id}")
            seen.add(item.id)

        unanswerable = len(self.of_kind(ItemKind.UNANSWERABLE))
        if self.items and unanswerable == 0:
            raise ValidationError(
                "the dataset contains no unanswerable items, so abstention cannot be "
                "measured -- a system that never refuses would score perfectly"
            )

    def resolve(self, chunks: Sequence[tuple[str, str]]) -> list[ResolvedItem]:
        """Bind quotes to chunk ids for one index.

        ``chunks`` is ``(chunk_id, text)``. Matching is substring-on-normalised-text,
        which tolerates the line breaks PDF extraction introduces and survives a
        change of chunk size — a longer chunk still contains the quote.
        """
        normalised = [(chunk_id, normalise(text)) for chunk_id, text in chunks]

        resolved: list[ResolvedItem] = []
        for item in self.items:
            matched: set[str] = set()
            unmatched: list[str] = []
            for quote in item.quotes:
                needle = normalise(quote)
                hits = [chunk_id for chunk_id, text in normalised if needle in text]
                if hits:
                    matched.update(hits)
                else:
                    unmatched.append(quote)
            resolved.append(
                ResolvedItem(
                    item=item,
                    relevant_chunk_ids=frozenset(matched),
                    unmatched_quotes=tuple(unmatched),
                )
            )
        return resolved

    # ------------------------------------------------------------- persistence
    @classmethod
    def load(cls, path: Path) -> GoldenDataset:
        payload = json.loads(path.read_text(encoding="utf-8"))
        dataset = cls(
            name=payload["name"],
            description=payload.get("description", ""),
            corpus=tuple(payload.get("corpus", ())),
            items=tuple(
                GoldenItem(
                    id=raw["id"],
                    question=raw["question"],
                    kind=ItemKind(raw["kind"]),
                    quotes=tuple(raw.get("quotes", ())),
                    expected_answer=raw.get("expected_answer"),
                    documents=tuple(raw.get("documents", ())),
                    notes=raw.get("notes", ""),
                )
                for raw in payload["items"]
            ),
        )
        dataset.validate()
        return dataset

    def save(self, path: Path) -> None:
        path.write_text(
            json.dumps(
                {
                    "name": self.name,
                    "description": self.description,
                    "corpus": list(self.corpus),
                    "items": [
                        {
                            "id": item.id,
                            "question": item.question,
                            "kind": item.kind.value,
                            "quotes": list(item.quotes),
                            "expected_answer": item.expected_answer,
                            "documents": list(item.documents),
                            "notes": item.notes,
                        }
                        for item in self.items
                    ],
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    @classmethod
    def of(cls, name: str, items: Iterable[GoldenItem], **rest: object) -> GoldenDataset:
        dataset = cls(name=name, items=tuple(items), **rest)  # type: ignore[arg-type]
        dataset.validate()
        return dataset
