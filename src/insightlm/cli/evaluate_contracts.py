"""`insightlm-eval-contracts`: measure clause judges against the labelled cases.

    insightlm-eval-contracts                  # offline baseline only, free
    insightlm-eval-contracts --judge gemini   # offline + Gemini; paid findings are cached

Paid findings are cached on disk, keyed by model, prompt version and case content, so a
rerun costs nothing until a case, the prompt or the model changes.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from insightlm.contracts.adapters.judges import (
    PROMPT_DIR,
    PROMPT_NAME,
    GeminiClauseJudge,
    OfflineClauseJudge,
)
from insightlm.contracts.domain.review import Finding
from insightlm.contracts.ports.judge import ClauseJudge
from insightlm.documents.domain.spans import TextSpan
from insightlm.evaluation.adapters.contract_cases import CONTRACT_CASES, KINDS, ClauseCase
from insightlm.retrieval.domain.evidence import (
    EvidenceBundle,
    EvidenceQuery,
    RetrievalStrategy,
    RetrievedSpan,
)
from insightlm.settings import get_settings
from insightlm.shared.adapters.prompts import load_prompt_from
from insightlm.shared.domain.ids import new_chunk_id, new_document_id, new_document_version_id

CACHE = Path("eval/.cache/clause_findings.json")


def _bundle(case: ClauseCase) -> EvidenceBundle:
    span = RetrievedSpan(
        label="C1",
        chunk_id=new_chunk_id(),
        document_id=new_document_id(),
        document_version_id=new_document_version_id(),
        document_title="contract.pdf",
        text=case.passage,
        span=TextSpan(char_start=0, char_end=len(case.passage), rects=()),
        score=0.8,
        section_label="Agreement",
    )
    return EvidenceBundle(
        query=EvidenceQuery(text=case.position.query),
        spans=(span,),
        strategy=RetrievalStrategy.DENSE,
        config_hash="contract-cases",
    )


def _key(model: str, prompt_version: str, case: ClauseCase) -> str:
    raw = f"{model}|{prompt_version}|{case.position.standard}|{case.passage}"
    return hashlib.sha256(raw.encode()).hexdigest()


async def _run(
    name: str,
    judge: ClauseJudge,
    cache: dict[str, str] | None,
    prompt_version: str,
    concurrency: int,
) -> list[tuple[ClauseCase, Finding]]:
    gate = asyncio.Semaphore(concurrency)

    async def one(case: ClauseCase) -> tuple[ClauseCase, Finding]:
        key = _key(name, prompt_version, case)
        if cache is not None and key in cache:
            return case, Finding(cache[key])
        async with gate:
            result = await judge.judge(case.position, _bundle(case))
        # A finding that cites nothing is discarded in production; score it the same way.
        finding = (
            result.finding
            if result.citation_labels or result.finding is Finding.MISSING
            else Finding.MISSING
        )
        if cache is not None:
            cache[key] = finding.value
        return case, finding

    return list(await asyncio.gather(*(one(c) for c in CONTRACT_CASES)))


def _report(name: str, outcomes: list[tuple[ClauseCase, Finding]]) -> dict[str, Any]:
    by_kind: dict[str, list[bool]] = defaultdict(list)
    for case, got in outcomes:
        by_kind[case.kind].append(got is case.expected)
    correct = [got is case.expected for case, got in outcomes]
    return {
        "judge": name,
        "cases": len(outcomes),
        "accuracy": sum(correct) / len(correct),
        "by_kind": {k: sum(v) / len(v) for k, v in by_kind.items()},
        "misses": [
            {
                "id": c.id,
                "kind": c.kind,
                "clause": c.position.clause,
                "expected": c.expected.value,
                "got": g.value,
            }
            for c, g in outcomes
            if g is not c.expected
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="insightlm-eval-contracts", description=__doc__)
    parser.add_argument("--judge", choices=("gemini",), help="Also run a model judge.")
    parser.add_argument("--no-cache", action="store_true", help="Ignore cached paid findings.")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", type=Path, help="Write the reports as JSON.")
    args = parser.parse_args(argv)

    reports = [
        _report(
            "offline (overlap)", asyncio.run(_run("offline", OfflineClauseJudge(), None, "none", 8))
        )
    ]
    if args.judge == "gemini":
        settings = get_settings()
        if settings.gemini_api_key is None:
            print("GEMINI_API_KEY is not set", file=sys.stderr)
            return 2
        cache: dict[str, str] = (
            {} if args.no_cache or not CACHE.exists() else json.loads(CACHE.read_text())
        )
        judge = GeminiClauseJudge(
            settings.gemini_api_key.get_secret_value(), model=settings.gemini_model
        )
        prompt_version = load_prompt_from(PROMPT_DIR, PROMPT_NAME).version_id
        before = len(cache)
        outcomes = asyncio.run(
            _run(settings.gemini_model, judge, cache, prompt_version, args.concurrency)
        )
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        CACHE.write_text(json.dumps(cache, indent=1, sort_keys=True))
        reports.append(_report(settings.gemini_model, outcomes))
        print(f"paid clause judgments this run: {len(cache) - before} (rest from cache)\n")

    print(f"contract-cases-v1: {len(CONTRACT_CASES)} cases\n")
    header = f"{'judge':22} {'acc':>5}  " + "  ".join(f"{k[:13]:>13}" for k in KINDS)
    print(header)
    print("-" * len(header))
    for r in reports:
        row = "  ".join(f"{r['by_kind'].get(k, 0):>13.0%}" for k in KINDS)
        print(f"{r['judge']:22} {r['accuracy']:>5.0%}  {row}")
    for r in reports:
        if r["misses"]:
            print(f"\n{r['judge']} missed {len(r['misses'])}:")
            for m in r["misses"]:
                print(
                    f"  {m['id']:4} {m['kind']:16} said {m['got']:9} wanted {m['expected']:9} {m['clause']}"
                )
    if args.out:
        args.out.write_text(json.dumps(reports, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
