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
import statistics
import sys
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import text

from insightlm.bootstrap import Container, build_container
from insightlm.documents.application.upload import UploadDocument
from insightlm.evaluation.adapters.corpus import SyntheticDocument, build_corpus
from insightlm.evaluation.adapters.generator import (
    build_generated_corpus,
    build_generated_dataset,
)
from insightlm.evaluation.adapters.golden import SEED_DATASET
from insightlm.evaluation.domain.dataset import GoldenDataset, ItemKind
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


async def _ingest_corpus(
    container: Container, actor: Actor, documents: list[SyntheticDocument]
) -> dict[str, str]:
    """Ingest every synthetic document; return slug -> document id for the ones that
    succeeded.

    The deliberately unreadable scan is expected to fail. It is left in because the
    harness should exercise the failure path, and because a corpus in which every
    document parses cleanly is not the corpus anyone has.
    """
    upload = UploadDocument(container.documents_uow, container.object_store, container.clock)  # type: ignore[arg-type]
    pipeline: IngestionPipeline = container.ingestion

    ingested: dict[str, str] = {}
    for document in documents:
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
    base_settings: Settings,
    dataset: GoldenDataset,
    config: EvalConfig,
    documents: list[SyntheticDocument],
    *,
    slug: str,
    generate: bool = False,
) -> EvalRun:
    # Chunking is part of the configuration, so the corpus is re-ingested per arm.
    # Sharing one index across arms that chunk differently would compare them against
    # different ground truth, which is the failure quote-based labelling exists to
    # avoid -- and it would silently invalidate the comparison rather than error.
    settings = base_settings.model_copy(
        update={"chunk_enrich_with_title": config.enrich_with_title}
    )
    container = build_container(settings)
    started = datetime.now(UTC)

    try:
        await container.object_store.ensure_bucket()
        actor = await _provision(container, slug)
        ingested = await _ingest_corpus(container, actor, documents)
        _logger.info("corpus_ingested", documents=len(ingested))

        chunks = await _load_chunks(container, actor)
        resolved = dataset.resolve(chunks)

        outcomes: list[ItemOutcome] = []
        for entry in resolved:
            item = entry.item
            request = AnswerRequest(
                question=item.question,
                top_k=config.top_k,
                strategy=RetrievalStrategy(config.strategy),
            )

            if not generate:
                try:
                    bundle = await container.ask.retrieve(actor, request)
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
                retrieved = [str(span.chunk_id) for span in bundle.spans]
                outcomes.append(
                    ItemOutcome(
                        item_id=item.id,
                        kind=item.kind,
                        answerable=item.answerable,
                        abstained=False,
                        retrieval=score_retrieval(
                            retrieved, set(entry.relevant_chunk_ids), k=config.top_k
                        ),
                        unmatched_quotes=entry.unmatched_quotes,
                    )
                )
                continue

            try:
                output = await container.ask.ask(actor, request)
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

        cache = container.embedder
        return EvalRun(
            config=config,
            dataset_name=dataset.name,
            started_at=started,
            outcomes=tuple(outcomes),
            generated=generate,
            provider_calls=getattr(cache, "misses", 0),
            cache_hits=getattr(cache, "hits", 0),
        )
    finally:
        await container.dispose()


def _fmt(value: float | None, places: int = 3) -> str:
    return "  -  " if value is None else f"{value:.{places}f}"


def _spread(values: list[float | None]) -> str:
    """Mean and half-range across repeats.

    Reported rather than a bare mean because generation is non-deterministic. Without
    a spread there is no way to tell a real improvement from one run getting lucky,
    and a table of bare means is the commonest way a project like this overclaims.
    """
    present = [value for value in values if value is not None]
    if not present:
        return "-"
    mean = statistics.fmean(present)
    if len(present) == 1:
        return f"{mean:.3f}"
    return f"{mean:.3f}+-{(max(present) - min(present)) / 2:.3f}"


def render_report(runs: list[EvalRun]) -> str:
    """The ablation table, as text.

    Deliberately plain: paste it into a dissertation, read it in a terminal, diff it
    between runs. Repeats of one configuration collapse into a single row.
    """
    grouped: dict[str, list[EvalRun]] = {}
    for run in runs:
        grouped.setdefault(run.config.name, []).append(run)

    width = 15 if any(len(group) > 1 for group in grouped.values()) else 8

    lines = ["", "  ablation"]
    header = (
        f"  {'config':<22} {'n':>3}  {'R@k':<{width}} {'nDCG':<{width}} "
        f"{'MRR':<{width}} {'cite-P':<{width}} {'refuse':<7} {'over-ref':<8} {'p50 ms':>7}"
    )
    lines.append(header)
    lines.append("  " + "-" * (len(header) - 2))

    for name, group in grouped.items():
        summaries = [run.summary() for run in group]
        latencies = [s.median_latency_ms for s in summaries if s.median_latency_ms]
        latency = f"{statistics.fmean(latencies):.0f}" if latencies else "-"
        generated = group[0].generated
        # Unmeasured is not zero. A retrieval-only run never asked the generator, so
        # its refusal rate is not 0.00 -- it does not exist, and printing a number
        # would invite someone to report it.
        gen = (lambda text: text) if generated else (lambda _text: "n/a")
        lines.append(
            f"  {name:<22} {summaries[0].items:>3}  "
            f"{_spread([s.recall_at_k for s in summaries]):<{width}} "
            f"{_spread([s.ndcg_at_k for s in summaries]):<{width}} "
            f"{_spread([s.mrr for s in summaries]):<{width}} "
            f"{gen(_spread([s.citation_precision for s in summaries])):<{width}} "
            f"{gen(_spread([s.abstention.correct_abstention_rate for s in summaries])[:5]):<7} "
            f"{gen(_spread([s.abstention.over_refusal_rate for s in summaries])[:5]):<8} "
            f"{latency:>7}"
        )

    for name, group in grouped.items():
        lines.append("")
        lines.append(f"  {name} -- {group[0].config.fingerprint()}")
        summaries = [run.summary() for run in group]
        head = summaries[0]
        violations = sum(s.unresolvable_citations + s.uncited_claims for s in summaries)
        lines.append(
            f"    citation contract held: {'yes' if violations == 0 else 'NO'}"
            f"   ({violations} violation(s) across {len(group)} run(s))"
        )
        unmatched = max(s.unmatched_quotes for s in summaries)
        if unmatched:
            lines.append(
                f"    WARNING: {unmatched} golden quote(s) matched no chunk -- recall "
                "is understated for those items"
            )
        if any(s.errors for s in summaries):
            lines.append(f"    errors: {sum(s.errors for s in summaries)}")
        lines.append(
            f"    tokens: {sum(s.total_input_tokens for s in summaries)} in / "
            f"{sum(s.total_output_tokens for s in summaries)} out   "
            f"texts embedded: {sum(r.provider_calls for r in group)} paid, "
            f"{sum(r.cache_hits for r in group)} cached"
        )
        lines.append("    by kind:")
        for kind in run_kinds(group):
            per_kind = [run.summary(kind=kind) for run in group]
            abstained = statistics.fmean(
                [sum(1 for o in run.outcomes if o.kind is kind and o.abstained) for run in group]
            )
            lines.append(
                f"      {kind.value:<14} n={per_kind[0].items:<3} "
                f"R@k={_spread([s.recall_at_k for s in per_kind]):<{width}} "
                f"nDCG={_spread([s.ndcg_at_k for s in per_kind]):<{width}} "
                f"abstained={abstained:.1f}"
            )
        _ = head

    paid = sum(run.provider_calls for run in runs)
    cached = sum(run.cache_hits for run in runs)
    generation_calls = sum(
        sum(1 for o in run.outcomes if o.input_tokens) for run in runs if run.generated
    )
    lines.append("")
    lines.append(
        f"  cost: {paid} text(s) embedded at the provider, {cached} from cache, "
        f"{generation_calls} generation call(s)"
    )
    lines.append("")
    return "\n".join(lines)


def run_kinds(group: list[EvalRun]) -> list[ItemKind]:
    seen: list[ItemKind] = []
    for run in group:
        for outcome in run.outcomes:
            if outcome.kind not in seen:
                seen.append(outcome.kind)
    return seen


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
    parser.add_argument("--dataset", type=Path, help="Golden set JSON. Overrides --corpus.")
    parser.add_argument(
        "--corpus",
        choices=("seed", "generated"),
        default="generated",
        help=(
            "seed: five hand-written documents, fast but recall-saturated. "
            "generated: a larger corpus of distractors where retrieval has to "
            "discriminate. Default generated."
        ),
    )
    parser.add_argument("--count", type=int, default=40, help="Documents in the generated corpus.")
    parser.add_argument(
        "--generate",
        action="store_true",
        help=(
            "Also call the generator, to measure abstention and citation behaviour. "
            "Off by default: retrieval metrics are computed before generation runs, so "
            "paying for generation to measure retrieval buys nothing."
        ),
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Disable the on-disk embedding cache (eval/.cache/embeddings.sqlite).",
    )
    parser.add_argument(
        "--runs",
        type=int,
        default=1,
        help=(
            "Repeat each configuration this many times and report the spread. "
            "Generation is non-deterministic, so a single run cannot separate a real "
            "effect from variance -- docs/03 section 5 calls for at least three."
        ),
    )
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
    if not args.no_cache:
        settings = settings.model_copy(
            update={"embedding_cache_path": str(Path("eval/.cache/embeddings.sqlite"))}
        )
    configure_logging(level="WARNING", json_output=False)

    if args.runs > 1 and not args.generate:
        # Retrieval is deterministic: identical input, identical embedding, identical
        # ranking. Repeating it measures nothing and, uncached, would pay again.
        print(
            "note: --runs has no effect without --generate -- retrieval is "
            "deterministic, so only generation has variance worth measuring",
            file=sys.stderr,
        )
        args.runs = 1

    if args.dataset:
        dataset = GoldenDataset.load(args.dataset)
        documents = build_generated_corpus(count=args.count)
    elif args.corpus == "seed":
        dataset, documents = SEED_DATASET, build_corpus()
    else:
        dataset = build_generated_dataset(count=args.count)
        documents = build_generated_corpus(count=args.count)
    dataset.validate()

    print(
        f"corpus: {len(documents)} documents   dataset: {dataset.name} "
        f"({len(dataset.items)} items)   runs: {args.runs}   "
        f"mode: {'retrieval + generation' if args.generate else 'retrieval only'}",
        file=sys.stderr,
    )

    selected = args.config or sorted(CONFIGS)
    runs: list[EvalRun] = []
    for index, name in enumerate(selected):
        for repeat in range(args.runs):
            label = f"{name} ({repeat + 1}/{args.runs})" if args.runs > 1 else name
            print(f"running {label} ...", file=sys.stderr)
            runs.append(
                await run_config(
                    settings,
                    dataset,
                    CONFIGS[name],
                    documents,
                    slug=f"eval-{datetime.now(UTC):%H%M%S}-{index}-{repeat}",
                    generate=args.generate,
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
        name="B - dense",
        strategy=RetrievalStrategy.DENSE.value,
        top_k=8,
        generator="configured",
        embedder="configured",
        enrich_with_title=False,
        notes="The Phase 1 baseline. Everything after is measured against this.",
    ),
    "C-dense-enriched": EvalConfig(
        name="C - dense + context",
        strategy=RetrievalStrategy.DENSE.value,
        top_k=8,
        generator="configured",
        embedder="configured",
        enrich_with_title=True,
        notes=(
            "Adds the document title to each chunk before embedding. Isolates the fix "
            "for the measured multi-hop failure: a passage describing an achievement "
            "says nothing about whose it is."
        ),
    ),
    "D-hybrid": EvalConfig(
        name="D - hybrid RRF",
        strategy=RetrievalStrategy.HYBRID_RRF.value,
        top_k=8,
        generator="configured",
        embedder="configured",
        enrich_with_title=True,
        notes="Adds the lexical arm, fused by rank. Isolates what fusion contributes.",
    ),
    "L-lexical": EvalConfig(
        name="L - lexical only",
        strategy=RetrievalStrategy.LEXICAL.value,
        top_k=8,
        generator="configured",
        embedder="configured",
        enrich_with_title=True,
        notes=(
            "A control rather than an arm: it shows what dense retrieval is buying, "
            "and which items it alone loses."
        ),
    ),
}


def main() -> None:
    raise SystemExit(asyncio.run(_main(sys.argv[1:])))


if __name__ == "__main__":
    main()
