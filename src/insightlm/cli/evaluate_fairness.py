"""`insightlm-eval-fairness`: does who the candidate is change their score?

    insightlm-eval-fairness                  # offline baseline; free (and trivially fair)
    insightlm-eval-fairness --judge gemini   # the real test; paid, cached

Counterfactual: each CV is scored in variants that differ in exactly one attribute
(name, institution, city), with blind screening off and on. The headline number per
attribute is the *spread*: how far apart the highest and lowest scoring variants of the
same CV are.

A spread only means something next to the judge's own noise. A model isn't
deterministic, so the reference CV is scored several more times unchanged; the spread
across those repeats is the noise floor. An attribute effect smaller than the noise
floor is not evidence of bias, and one larger is.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import random
import statistics
import sys
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

from insightlm.documents.domain.spans import PageRect, TextSpan
from insightlm.evaluation.adapters.fairness import (
    BASES,
    CRITERIA,
    NAMES,
    BaseCv,
    Dimension,
    Variant,
    variants,
)
from insightlm.evaluation.adapters.judge_cache import CachedJudge, JudgmentStore
from insightlm.governance.domain.redaction import BLIND, NO_REDACTION, RedactionProfile
from insightlm.recruiting.adapters.judges import (
    PROMPT_DIR,
    PROMPT_NAME,
    GeminiCriterionJudge,
    OfflineCriterionJudge,
)
from insightlm.recruiting.application.assess import _resolve, evidence_as_seen_by_judge
from insightlm.recruiting.domain.assessment import Candidate, CriterionJudgment, aggregate
from insightlm.recruiting.domain.rubric import Criterion, CriterionKind, Rubric
from insightlm.recruiting.ports.judge import CriterionJudge
from insightlm.retrieval.domain.evidence import (
    EvidenceBundle,
    EvidenceQuery,
    RetrievalStrategy,
    RetrievedSpan,
)
from insightlm.settings import get_settings
from insightlm.shared.adapters.prompts import load_prompt_from
from insightlm.shared.domain.ids import (
    new_candidate_id,
    new_chunk_id,
    new_criterion_id,
    new_document_id,
    new_document_version_id,
    new_requisition_id,
    new_rubric_id,
    new_tenant_id,
    new_user_id,
)

AT = datetime(2026, 9, 19, tzinfo=UTC)


def _rubric() -> Rubric:
    user = new_user_id()
    return Rubric(
        id=new_rubric_id(),
        tenant_id=new_tenant_id(),
        requisition_id=new_requisition_id(),
        version=1,
        criteria=tuple(
            Criterion(
                id=new_criterion_id(),
                label=label,
                kind=CriterionKind(kind),
                weight=weight,
                evidence_hint=hint,
            )
            for label, kind, weight, hint in CRITERIA
        ),
        proposed_by=user,
        created_at=AT,
    ).approve(by=user, at=AT, may_self_approve=True)


def _bundle(passages: tuple[str, ...], criterion: Criterion) -> EvidenceBundle:
    return EvidenceBundle(
        query=EvidenceQuery(text=criterion.query),
        spans=tuple(
            RetrievedSpan(
                label=f"C{i + 1}",
                chunk_id=new_chunk_id(),
                document_id=new_document_id(),
                document_version_id=new_document_version_id(),
                document_title="CV",
                text=text,
                span=TextSpan(
                    char_start=0,
                    char_end=len(text),
                    rects=(PageRect(page=0, x0=0, y0=0, x1=1, y1=1),),
                ),
                score=1.0,
            )
            for i, text in enumerate(passages)
        ),
        strategy=RetrievalStrategy.DENSE,
        config_hash="fairness-eval",
    )


async def _score(
    variant: Variant, rubric: Rubric, judge: CriterionJudge, profile: RedactionProfile
) -> float:
    candidate = Candidate(
        id=new_candidate_id(),
        tenant_id=rubric.tenant_id,
        name=variant.identity.name,
        email=None,
        document_ids=(new_document_id(),),
        created_at=AT,
    )

    async def one(criterion: Criterion) -> CriterionJudgment:
        evidence = _bundle(variant.passages, criterion)
        shown = evidence_as_seen_by_judge(evidence, profile, candidate)
        return _resolve(criterion, await judge.judge(criterion, shown), evidence)

    judgments = await asyncio.gather(*(one(c) for c in rubric.criteria))
    score, _ = aggregate(rubric, list(judgments))
    return score


def _permutation_p(groups: dict[str, list[float]], rounds: int = 5000) -> float | None:
    """How often random relabelling produces a spread of group means this large.

    Pools every sample, shuffles which variant each belongs to, and recomputes the
    range of group means. A small p says the variants differ by more than the judge's
    own noise explains. Seeded, so the report is reproducible.
    """
    sizes = [len(v) for v in groups.values()]
    pooled = [x for v in groups.values() for x in v]
    if len(groups) < 2 or len(set(pooled)) < 2:
        return None

    def spread(values: list[float]) -> float:
        means, i = [], 0
        for n in sizes:
            means.append(statistics.fmean(values[i : i + n]))
            i += n
        return max(means) - min(means)

    observed = spread(pooled)
    rng = random.Random(20260919)  # noqa: S311 -- a seeded shuffle for statistics, not secrets
    shuffled = list(pooled)
    extreme = 0
    for _ in range(rounds):
        rng.shuffle(shuffled)
        if spread(shuffled) >= observed - 1e-9:
            extreme += 1
    return (extreme + 1) / (rounds + 1)


async def _measure(
    judges: Sequence[CriterionJudge], profile: RedactionProfile, bases: Sequence[BaseCv]
) -> dict[str, object]:
    """Every variant scored once per judge (one judge per sample), grouped by attribute."""
    rubric = _rubric()
    out: dict[str, object] = {"profile": profile.id, "bases": {}}
    for base in bases:
        samples: dict[str, dict[str, list[float]]] = defaultdict(dict)
        for variant in variants(base):
            key = variant.dimension.value if variant.dimension else "reference"
            samples[key][variant.value] = [
                await _score(variant, rubric, judge, profile) for judge in judges
            ]
        per_dimension: dict[str, object] = {}
        for dimension in Dimension:
            groups = samples[dimension.value]
            means = {k: statistics.fmean(v) for k, v in groups.items()}
            per_dimension[dimension.value] = {
                "means": means,
                "spread_of_means": max(means.values()) - min(means.values()),
                "p": _permutation_p(groups),
            }
        reference = samples["reference"]["reference"]
        per_dimension["noise_sd"] = statistics.pstdev(reference) if len(reference) > 1 else 0.0
        per_dimension["reference"] = reference
        names = samples[Dimension.NAME.value]
        women = [x for who in NAMES if who.subject == "she" for x in names[who.name]]
        men = [x for who in NAMES if who.subject == "he" for x in names[who.name]]
        per_dimension["gender_gap"] = {
            "men_minus_women": statistics.fmean(men) - statistics.fmean(women),
            "p": _permutation_p({"men": men, "women": women}),
        }
        out["bases"][base.id] = per_dimension  # type: ignore[index]
    return out


def _fmt_p(p: object) -> str:
    return "  n/a" if p is None else f"{p:5.3f}"


def _print(name: str, results: list[dict[str, object]]) -> None:
    for result in results:
        print(f"\njudge {name}, redaction {result['profile']}")
        for base_id, dims in result["bases"].items():  # type: ignore[attr-defined]
            print(
                f"  {base_id}: reference samples {[round(x) for x in dims['reference']]}"
                f", noise sd {dims['noise_sd']:.1f}"
            )
            for dimension in Dimension:
                d = dims[dimension.value]
                means = ", ".join(f"{k.split()[0]} {v:.0f}" for k, v in d["means"].items())
                print(
                    f"    {dimension.value:<12} spread {d['spread_of_means']:5.1f}  "
                    f"p {_fmt_p(d['p'])}   {means}"
                )
            gap = dims["gender_gap"]
            print(
                f"    {'gender':<12} men-women {gap['men_minus_women']:+5.1f}  p {_fmt_p(gap['p'])}"
            )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="insightlm-eval-fairness", description=__doc__)
    parser.add_argument("--judge", choices=["gemini"], help="Add a model judge (paid, cached).")
    parser.add_argument("--out", type=Path, help="Write full results as JSON.")
    parser.add_argument("--samples", type=int, default=5, help="Model samples per variant.")
    parser.add_argument("--bases", nargs="*", help="Only these base CVs (strong, ambiguous, weak).")
    args = parser.parse_args(argv)

    bases = [b for b in BASES if not args.bases or b.id in args.bases]
    report: dict[str, list[dict[str, object]]] = {}
    offline = OfflineCriterionJudge()
    report["offline"] = [
        asyncio.run(_measure([offline], profile, bases)) for profile in (NO_REDACTION, BLIND)
    ]
    _print("offline (overlap)", report["offline"])

    if args.judge == "gemini":
        settings = get_settings()
        if settings.gemini_api_key is None:
            parser.error("--judge gemini needs GEMINI_API_KEY")
        store = JudgmentStore()
        prompt = load_prompt_from(PROMPT_DIR, PROMPT_NAME).version_id
        inner = GeminiCriterionJudge(
            settings.gemini_api_key.get_secret_value(), model=settings.gemini_model
        )
        # Sample 0 is unsalted so it shares cache entries with earlier runs.
        judges = [
            CachedJudge(
                inner,
                store,
                model=settings.gemini_model,
                prompt=prompt,
                salt="" if i == 0 else f"repeat-{i}",
            )
            for i in range(args.samples)
        ]

        # One event loop for both passes: the SDK client holds connections bound to
        # the loop that opened them, and a second asyncio.run finds them closed.
        async def both() -> list[dict[str, object]]:
            return [await _measure(judges, profile, bases) for profile in (NO_REDACTION, BLIND)]

        try:
            report[settings.gemini_model] = asyncio.run(both())
        finally:
            store.save()
            print(f"\npaid judge calls this run: {sum(j.calls for j in judges)} (rest from cache)")
        _print(settings.gemini_model, report[settings.gemini_model])

    if args.out:
        args.out.write_text(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
