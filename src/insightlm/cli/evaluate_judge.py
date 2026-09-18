"""`insightlm-eval-judge`: measure criterion judges against the labelled cases.

    insightlm-eval-judge                     # offline baseline only, free
    insightlm-eval-judge --judge gemini      # offline + Gemini; paid calls are cached

Paid verdicts are cached on disk, keyed by model, prompt version and case content, so
a rerun costs nothing until a case, the prompt or the model changes. That makes this
cheap enough to run after every prompt edit, which is the point: a judge prompt that
isn't measured drifts.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from insightlm.documents.domain.spans import PageRect, TextSpan
from insightlm.evaluation.adapters.judge_cases import JUDGE_V1
from insightlm.evaluation.domain.judging import CaseKind, JudgeCase, JudgeOutcome, JudgeReport
from insightlm.recruiting.adapters.judges import (
    PROMPT_DIR,
    PROMPT_NAME,
    AnthropicCriterionJudge,
    GeminiCriterionJudge,
    OfflineCriterionJudge,
)
from insightlm.recruiting.domain.assessment import Verdict
from insightlm.recruiting.domain.rubric import Criterion, CriterionKind
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
    new_chunk_id,
    new_criterion_id,
    new_document_id,
    new_document_version_id,
)

CACHE = Path("eval/.cache/judgments.json")


def _bundle(case: JudgeCase) -> EvidenceBundle:
    spans = tuple(
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
            score=1.0 - i * 0.1,
        )
        for i, text in enumerate(case.passages)
    )
    return EvidenceBundle(
        query=EvidenceQuery(text=case.criterion),
        spans=spans,
        strategy=RetrievalStrategy.DENSE,
        config_hash="judge-eval",
    )


def _criterion(case: JudgeCase) -> Criterion:
    return Criterion(
        id=new_criterion_id(),
        label=case.criterion,
        kind=CriterionKind.STRONG_PREFERENCE,
        weight=1.0,
        evidence_hint=case.hint,
    )


@dataclass(slots=True)
class _Cache:
    path: Path
    entries: dict[str, dict[str, Any]]
    enabled: bool

    @classmethod
    def load(cls, path: Path, *, enabled: bool) -> _Cache:
        entries = json.loads(path.read_text()) if enabled and path.exists() else {}
        return cls(path, entries, enabled)

    def save(self) -> None:
        if self.enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.entries, indent=1, sort_keys=True))


def _key(model: str, prompt: str, case: JudgeCase) -> str:
    raw = json.dumps([model, prompt, case.criterion, case.hint, list(case.passages)])
    return hashlib.sha256(raw.encode()).hexdigest()


async def _run(
    name: str, judge: CriterionJudge, model: str, cache: _Cache | None, concurrency: int
) -> tuple[JudgeReport, int]:
    prompt = load_prompt_from(PROMPT_DIR, PROMPT_NAME).version_id
    report = JudgeReport(judge=name)
    gate = asyncio.Semaphore(concurrency)
    calls = 0

    async def one(case: JudgeCase) -> JudgeOutcome:
        nonlocal calls
        key = _key(model, prompt, case)
        if cache is not None and key in cache.entries:
            hit = cache.entries[key]
            return JudgeOutcome(case, Verdict(hit["verdict"]), tuple(hit["cited"]))
        async with gate:
            result = await judge.judge(_criterion(case), _bundle(case))
        calls += 1 if cache is not None else 0
        cited = tuple(dict.fromkeys(label.strip().upper() for label in result.citation_labels))
        if cache is not None:
            cache.entries[key] = {
                "verdict": result.verdict.value,
                "cited": list(cited),
                "rationale": result.rationale,
                "case": case.id,
            }
        return JudgeOutcome(case, result.verdict, cited)

    report.outcomes = list(await asyncio.gather(*(one(c) for c in JUDGE_V1)))
    return report, calls


def _pct(value: float | None) -> str:
    return "  n/a" if value is None else f"{value:5.0%}"


def _print(reports: list[JudgeReport]) -> None:
    kinds = list(CaseKind)
    header = f"{'judge':<22}{'acc':>6}{'detect':>8}{'cite':>7}  " + "".join(
        f"{k.value[:10]:>11}" for k in kinds
    )
    print(header)
    print("-" * len(header))
    for report in reports:
        by_kind = report.by_kind()
        print(
            f"{report.judge:<22}{_pct(report.accuracy):>6}{_pct(report.detection):>8}"
            f"{_pct(report.citation_precision):>7}  "
            + "".join(f"{_pct(by_kind[k]):>11}" for k in kinds)
        )
    for report in reports:
        if report.misses:
            print(f"\n{report.judge} missed {len(report.misses)}:")
            for miss in report.misses:
                accepted = "/".join(sorted(v.value for v in miss.case.acceptable))
                print(
                    f"  {miss.case.id:<4} {miss.case.kind.value:<10} "
                    f"said {miss.verdict.value:<8} wanted {accepted:<16} {miss.case.criterion}"
                )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="insightlm-eval-judge", description=__doc__)
    parser.add_argument(
        "--judge",
        choices=["offline", "gemini", "anthropic"],
        action="append",
        help="Judges to measure alongside the offline baseline (repeatable).",
    )
    parser.add_argument("--no-cache", action="store_true", help="Ignore cached paid verdicts.")
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--out", type=Path, help="Write the reports as JSON.")
    parser.add_argument(
        "--min-accuracy", type=float, help="Fail if any paid judge scores below this."
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    wanted = set(args.judge or [])
    runs: list[tuple[str, CriterionJudge, str, bool]] = [
        ("offline (overlap)", OfflineCriterionJudge(), OfflineCriterionJudge.MODEL_ID, False)
    ]
    if "gemini" in wanted:
        if settings.gemini_api_key is None:
            parser.error("--judge gemini needs GEMINI_API_KEY")
        runs.append(
            (
                settings.gemini_model,
                GeminiCriterionJudge(
                    settings.gemini_api_key.get_secret_value(), model=settings.gemini_model
                ),
                settings.gemini_model,
                True,
            )
        )
    if "anthropic" in wanted:
        if settings.anthropic_api_key is None:
            parser.error("--judge anthropic needs ANTHROPIC_API_KEY")
        runs.append(
            (
                settings.generation_model,
                AnthropicCriterionJudge(
                    settings.anthropic_api_key.get_secret_value(), model=settings.generation_model
                ),
                settings.generation_model,
                True,
            )
        )

    cache = _Cache.load(CACHE, enabled=not args.no_cache)
    reports: list[JudgeReport] = []
    total_calls = 0
    for name, judge, model, paid in runs:
        report, calls = asyncio.run(
            _run(name, judge, model, cache if paid else None, args.concurrency)
        )
        reports.append(report)
        total_calls += calls
    cache.save()

    print(f"judge-v1: {len(JUDGE_V1)} cases\n")
    _print(reports)
    print(f"\npaid judge calls this run: {total_calls} (rest from cache)")

    if args.out:
        args.out.write_text(
            json.dumps(
                [
                    {
                        "judge": r.judge,
                        "accuracy": r.accuracy,
                        "detection": r.detection,
                        "citation_precision": r.citation_precision,
                        "by_kind": {k.value: v for k, v in r.by_kind().items()},
                        "misses": [m.case.id for m in r.misses],
                    }
                    for r in reports
                ],
                indent=2,
            )
        )

    paid_reports = [r for r, (_, _, _, paid) in zip(reports, runs, strict=True) if paid]
    if args.min_accuracy is not None and any(
        (r.accuracy or 0) < args.min_accuracy for r in paid_reports
    ):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
