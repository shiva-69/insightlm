"""The evaluation runner.

Composition, like the ingestion pipeline: it drives `documents`, `retrieval` and
`evaluation` together, and no module may compose another. It lives here for the same
reason the pipeline lives in the worker.

What it does, per configuration:

1. Ingest the synthetic corpus into a throwaway tenant, through the *real* pipeline.
   Evaluating against a hand-built index would measure a system nobody runs.
2. Resolve each item's quotes against the chunks that configuration produced.
3. Ask each question through the real ask path, and score what comes back.

Step 1 is why this is slower than a unit test and why it belongs behind a CLI rather
than in the default suite. The reduced set that gates CI is a subset of the same
thing, not a different thing.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from insightlm.bootstrap import Container, build_container
from insightlm.documents.application.upload import UploadDocument
from insightlm.evaluation.adapters.corpus import build_corpus
from insightlm.evaluation.adapters.golden import SEED_DATASET
from insightlm.evaluation.domain.dataset import GoldenDataset
from insightlm.evaluation.domain.grounding import score_citations
from insightlm.evaluation.domain.metrics import score_retrieval
from insightlm.evaluation.domain.run import (
    EvalConfig,
    EvalRun,
    ItemOutcome,
    RunSummary,
)
from insightlm.identity.application.commands import RegisterOwnerCommand
from insightlm.retrieval.application.answer import AnswerRequest
from insightlm.retrieval.domain.evidence import RetrievalStrategy
from insightlm.settings import Settings, get_settings
from insightlm.shared.domain.policy import Actor
from insightlm.shared.domain.roles import Role
from insightlm.shared.observability.logging import configure_logging, get_logger
from insightlm.worker.ingestion import IngestionPipeline

_logger = get_logger(__name__)

# Not a credential: the harness creates a throwaway tenant per run and nothing ever
# signs into it. Named rather than inlined so that is obvious at the call site.
EVAL_PASSWORD = "evaluation-harness-not-a-real-account"  # noqa: S105


async def _provision(container: Container, slug: str) -> Actor:
    result = await container.register_owner.execute(
        RegisterOwnerCommand(
            tenant_name=f"Eval {slug}",
            tenant_slug=slug,
            email=f"harness+{slug}@insightlm.invalid",
            display_name="Evaluation harness",
            password=EVAL_PASSWORD,
        )
    )
    return Actor(user_id=result.user.id, tenant_id=result.tenant.id, role=Role.OWNER)


async def _ingest_corpus(container: Container, actor: Actor) -> dict[str, str]:
    """Ingest every synthetic document; return slug -> document id for the ones that
    succeeded.

    The deliberately unreadable scan is expected to fail. It is left in because the
    harness should exercise the failure path, and because a corpus in which every
    document parses cleanly is not the corpus anyone has.
    """
    upload = UploadDocument(container.documents_uow, container.object_store, container.clock)  # type: ignore[arg-type]
    pipeline: IngestionPipeline = container.ingestion

    ingested: dict[str, str] = {}
    for document in build_corpus():
        result = await upload.execute(
            actor,
            filename=f"{document.slug}.pdf",
            payload=document.payload,
            title=document.title,
        )
        outcome = await pipeline.run(tenant_id=actor.tenant_id, version_id=result.version.id)
        if outcome.completed:
            ingested[document.slug] = str(result.document.id)
        else:
            _logger.warning("corpus_document_failed", slug=document.slug, error=outcome.error)
    return ingested


async def _load_chunks(container: Container, actor: Actor) -> list[tuple[str, str]]:
    """Every chunk in the tenant, as (id, text), for quote resolution."""
    async with container.documents_uow(tenant_id=actor.tenant_id) as uow:  # type: ignore[operator]
        rows = await uow.session.execute(
            text("SELECT id, body FROM chunks WHERE tenant_id = CAST(:t AS uuid)"),
            {"t": str(actor.tenant_id)},
        )
        return [(str(row.id), row.body) for row in rows]


async def run_config(
    settings: Settings, dataset: GoldenDataset, config: EvalConfig, *, slug: str
) -> EvalRun:
    container = build_container(settings)
    started = datetime.now(UTC)

    try:
        await container.object_store.ensure_bucket()
        actor = await _provision(container, slug)
        ingested = await _ingest_corpus(container, actor)
        _logger.info("corpus_ingested", documents=len(ingested))

        chunks = await _load_chunks(container, actor)
        resolved = dataset.resolve(chunks)

        outcomes: list[ItemOutcome] = []
        for entry in resolved:
            item = entry.item
            try:
                output = await container.ask.ask(
                    actor,
                    AnswerRequest(
                        question=item.question,
                        top_k=config.top_k,
                        strategy=RetrievalStrategy(config.strategy),
                    ),
                )
            except Exception as cause:
                outcomes.append(
                    ItemOutcome(
                        item_id=item.id,
                        kind=item.kind,
                        answerable=item.answerable,
                        abstained=False,
                        error=f"{type(cause).__name__}: {cause}",
                    )
                )
                continue

            answer = output.answer
            retrieved = [str(span.chunk_id) for span in answer.evidence.spans]
            cited = [str(c.chunk_id) for c in answer.citations]
            relevant = set(entry.relevant_chunk_ids)

            outcomes.append(
                ItemOutcome(
                    item_id=item.id,
                    kind=item.kind,
                    answerable=item.answerable,
                    abstained=answer.outcome.value != "answered",
                    retrieval=score_retrieval(retrieved, relevant, k=config.top_k),
                    citations=score_citations(
                        cited=cited,
                        relevant=relevant,
                        retrieved=retrieved,
                        claims_total=len(answer.claims),
                        claims_uncited=sum(1 for c in answer.claims if not c.citations),
                    ),
                    unmatched_quotes=entry.unmatched_quotes,
                    latency_ms=output.latency_ms,
                    input_tokens=answer.input_tokens,
                    output_tokens=answer.output_tokens,
                )
            )

        return EvalRun(
            config=config,
            dataset_name=dataset.name,
            started_at=started,
            outcomes=tuple(outcomes),
        )
    finally:
        await container.dispose()


def _fmt(value: float | None, places: int = 3) -> str:
    return "  -  " if value is None else f"{value:.{places}f}"


def render_report(runs: list[EvalRun]) -> str:
    """The ablation table, as text.

    Deliberately plain: a table you can paste into a dissertation, read in a terminal,
    and diff between runs.
    """
    lines: list[str] = []
    lines.append("")
    lines.append(
        "  config                  items  R@k    nDCG   MRR    MAP    cite-P  refuse  over-ref  p50 ms"
    )
    lines.append("  " + "-" * 92)

    for run in runs:
        s = run.summary()
        lines.append(
            f"  {run.config.name:<22}  {s.items:>5}  "
            f"{_fmt(s.recall_at_k)}  {_fmt(s.ndcg_at_k)}  {_fmt(s.mrr)}  "
            f"{_fmt(s.map_score)}  {_fmt(s.citation_precision)}   "
            f"{_fmt(s.abstention.correct_abstention_rate, 2)}   "
            f"{_fmt(s.abstention.over_refusal_rate, 2)}     "
            f"{'  -  ' if s.median_latency_ms is None else f'{s.median_latency_ms:.0f}'}"
        )

    for run in runs:
        lines.append("")
        lines.append(f"  {run.config.name} — {run.config.fingerprint()}")
        s = run.summary()
        lines.append(
            f"    citation contract held: {'yes' if s.contract_held else 'NO'}"
            f"   (unresolvable {s.unresolvable_citations}, uncited {s.uncited_claims})"
        )
        if s.unmatched_quotes:
            lines.append(
                f"    WARNING: {s.unmatched_quotes} golden quote(s) matched no chunk — "
                "recall is understated for those items"
            )
        if s.errors:
            lines.append(f"    errors: {s.errors}")
        lines.append(f"    tokens: {s.total_input_tokens} in / {s.total_output_tokens} out")
        lines.append("    by kind:")
        for kind, summary in run.breakdown().items():
            lines.append(
                f"      {kind.value:<14} n={summary.items:<3} "
                f"R@k={_fmt(summary.recall_at_k)}  "
                f"nDCG={_fmt(summary.ndcg_at_k)}  "
                f"abstained={sum(1 for o in run.outcomes if o.kind is kind and o.abstained)}"
            )

    lines.append("")
    return "\n".join(lines)


def _summary_payload(summary: RunSummary) -> dict[str, object]:
    payload = asdict(summary)
    payload["abstention"] = asdict(summary.abstention)
    payload["contract_held"] = summary.contract_held
    return payload


async def _main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="insightlm-eval",
        description="Run the golden set against one or more pipeline configurations.",
    )
    parser.add_argument(
        "--config",
        action="append",
        choices=sorted(CONFIGS),
        help="Configuration to run; repeatable. Defaults to all.",
    )
    parser.add_argument("--dataset", type=Path, help="Golden set JSON. Defaults to the seed set.")
    parser.add_argument("--out", type=Path, help="Write the run as JSON here.")
    parser.add_argument(
        "--min-recall",
        type=float,
        help=(
            "Fail if macro recall@k falls below this. Note that recall saturates on a "
            "small corpus -- see eval/baselines.json before gating on it."
        ),
    )
    parser.add_argument(
        "--min-ndcg",
        type=float,
        help=(
            "Fail if macro nDCG@k falls below this. The CI regression gate: unlike "
            "recall it is sensitive to ordering, so it can still detect a regression "
            "on a corpus where everything relevant is returned anyway."
        ),
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    configure_logging(level="WARNING", json_output=False)

    dataset = GoldenDataset.load(args.dataset) if args.dataset else SEED_DATASET
    dataset.validate()

    selected = args.config or sorted(CONFIGS)
    runs: list[EvalRun] = []
    for index, name in enumerate(selected):
        print(f"running {name} ...", file=sys.stderr)
        runs.append(
            await run_config(
                settings,
                dataset,
                CONFIGS[name],
                slug=f"eval-{datetime.now(UTC):%H%M%S}-{index}",
            )
        )

    report = render_report(runs)
    print(report)

    if args.out:
        args.out.write_text(
            json.dumps(
                {
                    "dataset": dataset.name,
                    "runs": [
                        {
                            "config": asdict(run.config),
                            "started_at": run.started_at.isoformat(),
                            "summary": _summary_payload(run.summary()),
                            "by_kind": {
                                kind.value: _summary_payload(summary)
                                for kind, summary in run.breakdown().items()
                            },
                            "items": [asdict(outcome) for outcome in run.outcomes],
                        }
                        for run in runs
                    ],
                },
                indent=2,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        print(f"written to {args.out}", file=sys.stderr)

    gates: list[tuple[str, float | None]] = [
        ("recall@k", args.min_recall),
        ("nDCG@k", args.min_ndcg),
    ]
    failed = False
    for run in runs:
        summary = run.summary()
        measured = {"recall@k": summary.recall_at_k, "nDCG@k": summary.ndcg_at_k}
        for label, floor in gates:
            if floor is None:
                continue
            value = measured[label]
            if value is None or value < floor:
                print(
                    f"FAIL {run.config.name}: {label} {_fmt(value)} is below the gate "
                    f"of {floor:.3f}",
                    file=sys.stderr,
                )
                failed = True
        # A broken citation contract fails regardless of any threshold: it is an
        # invariant, not a quality target.
        if not summary.contract_held:
            print(
                f"FAIL {run.config.name}: the citation contract was violated "
                f"({summary.unresolvable_citations} unresolvable, "
                f"{summary.uncited_claims} uncited)",
                file=sys.stderr,
            )
            failed = True

    if failed:
        return 1
    if any(floor is not None for _, floor in gates):
        print("regression gate passed", file=sys.stderr)

    return 0


CONFIGS: dict[str, EvalConfig] = {
    "B-dense": EvalConfig(
        name="B — naive dense",
        strategy=RetrievalStrategy.DENSE.value,
        top_k=8,
        generator="configured",
        embedder="configured",
        notes="The Phase 1 baseline. Everything Phase 3 adds is measured against this.",
    ),
    "L-lexical": EvalConfig(
        name="L — lexical only",
        strategy=RetrievalStrategy.LEXICAL.value,
        top_k=8,
        generator="configured",
        embedder="configured",
        notes=(
            "Full-text search alone. Not an ablation arm in its own right -- it is the "
            "control that shows what dense retrieval is actually buying, and which "
            "items it loses."
        ),
    ),
}


def main() -> None:
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
