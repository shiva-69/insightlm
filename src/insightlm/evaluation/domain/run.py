"""An evaluation run: what was measured, under what configuration, and the result.

The configuration is recorded in full alongside the numbers. A results table without
the configuration that produced it is not a result -- it is an anecdote, and it cannot
be re-run six weeks later when a reviewer asks whether reranking actually helped.

Aggregation is macro-average over items, and undefined metrics are *excluded* rather
than counted as zero. Unanswerable items have no relevant passages, so their recall is
undefined; folding them in as zeroes would make every configuration look worse in
proportion to how many unanswerable items the set happens to contain.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime

from insightlm.evaluation.domain.dataset import ItemKind
from insightlm.evaluation.domain.grounding import (
    AbstentionOutcome,
    AbstentionRates,
    CitationScores,
    score_abstention,
)
from insightlm.evaluation.domain.metrics import RetrievalScores


@dataclass(frozen=True, slots=True)
class EvalConfig:
    """One point in the ablation matrix (docs/03 SS5)."""

    name: str
    strategy: str
    top_k: int
    generator: str
    embedder: str
    chunker: str = "structure-aware"
    enrich_with_title: bool = True
    """Whether each chunk carries its document's title into the embedding. An arm,
    not a setting: the harness has to measure what it buys."""
    notes: str = ""

    def fingerprint(self) -> str:
        enrichment = "+ctx" if self.enrich_with_title else "-ctx"
        return f"{self.strategy}{enrichment}/k={self.top_k}/{self.embedder}/{self.generator}"


@dataclass(frozen=True, slots=True)
class ItemOutcome:
    item_id: str
    kind: ItemKind
    answerable: bool
    abstained: bool
    retrieval: RetrievalScores | None = None
    citations: CitationScores | None = None
    unmatched_quotes: tuple[str, ...] = ()
    latency_ms: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    error: str | None = None

    @property
    def abstention(self) -> AbstentionOutcome:
        return AbstentionOutcome(answerable=self.answerable, abstained=self.abstained)


def _mean(values: Sequence[float | None]) -> float | None:
    """Macro-average, skipping undefined values.

    Skipping rather than zero-filling: a metric that does not apply to an item should
    not drag that item's neighbours down.
    """
    present = [value for value in values if value is not None]
    return statistics.fmean(present) if present else None


@dataclass(frozen=True, slots=True)
class RunSummary:
    items: int
    errors: int
    recall_at_k: float | None
    precision_at_k: float | None
    mrr: float | None
    ndcg_at_k: float | None
    map_score: float | None
    hit_rate: float | None
    citation_precision: float | None
    citation_recall: float | None
    unresolvable_citations: int
    uncited_claims: int
    unmatched_quotes: int
    abstention: AbstentionRates
    median_latency_ms: float | None
    total_input_tokens: int
    total_output_tokens: int

    @property
    def contract_held(self) -> bool:
        """Whether the citation contract was violated anywhere in this run.

        Both counts must be zero by construction -- the domain refuses to build an
        uncited claim, and the validator drops unresolvable citations before display.
        Measuring it is how we find out if that is still true.
        """
        return self.unresolvable_citations == 0 and self.uncited_claims == 0


@dataclass(frozen=True, slots=True)
class EvalRun:
    config: EvalConfig
    dataset_name: str
    started_at: datetime
    outcomes: tuple[ItemOutcome, ...] = field(default=())

    def summary(self, *, kind: ItemKind | None = None) -> RunSummary:
        """Aggregate, optionally for one item kind.

        The per-kind breakdown is where the interesting findings live: a
        configuration can lift the headline average while getting *worse* at
        multi-hop, and only the breakdown shows it.
        """
        selected = [outcome for outcome in self.outcomes if kind is None or outcome.kind is kind]
        retrieval = [o.retrieval for o in selected if o.retrieval is not None]
        citations = [o.citations for o in selected if o.citations is not None]
        latencies = [float(o.latency_ms) for o in selected if o.latency_ms > 0]

        return RunSummary(
            items=len(selected),
            errors=sum(1 for o in selected if o.error is not None),
            recall_at_k=_mean([r.recall for r in retrieval]),
            precision_at_k=_mean([r.precision for r in retrieval]),
            mrr=_mean([r.mrr for r in retrieval]),
            ndcg_at_k=_mean([r.ndcg for r in retrieval]),
            map_score=_mean([r.average_precision for r in retrieval]),
            hit_rate=_mean([r.hit_rate for r in retrieval]),
            citation_precision=_mean([c.citation_precision for c in citations]),
            citation_recall=_mean([c.citation_recall for c in citations]),
            unresolvable_citations=sum(c.citations_unresolvable for c in citations),
            uncited_claims=sum(c.claims_uncited for c in citations),
            unmatched_quotes=sum(len(o.unmatched_quotes) for o in selected),
            abstention=score_abstention([o.abstention for o in selected]),
            median_latency_ms=statistics.median(latencies) if latencies else None,
            total_input_tokens=sum(o.input_tokens for o in selected),
            total_output_tokens=sum(o.output_tokens for o in selected),
        )

    def breakdown(self) -> dict[ItemKind, RunSummary]:
        return {
            kind: self.summary(kind=kind)
            for kind in ItemKind
            if any(o.kind is kind for o in self.outcomes)
        }
