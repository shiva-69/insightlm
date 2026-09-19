"""`insightlm-eval-talent`: where should talent search stop returning people?

    insightlm-eval-talent                  # offline embedder; free
    insightlm-eval-talent --embedder gemini  # real embeddings; cached after one run

Uses cohort-v1, where it's known exactly who has each skill. Each query is scored the
way production scores it -- a candidate's best cosine similarity over their chunks --
and cut off two ways: an absolute similarity floor, and a floor relative to the best
match for that query. Reports precision, recall and F1 at each cutoff, so the setting
is chosen from numbers rather than from what looked right on three results.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import statistics
import sys
from pathlib import Path

from insightlm.bootstrap import _build_embedder
from insightlm.documents.adapters.pymupdf_parser import PyMuPdfParser
from insightlm.documents.domain.entities import MediaType
from insightlm.evaluation.adapters.cohort import BLOCKS, build_cohort
from insightlm.retrieval.adapters.chunker import StructureAwareChunker
from insightlm.retrieval.adapters.embedding import HashingEmbedder
from insightlm.retrieval.adapters.embedding_cache import CachingEmbedder, EmbeddingStore
from insightlm.retrieval.ports.indexing import Embedder
from insightlm.settings import get_settings
from insightlm.shared.domain.ids import new_document_version_id, new_tenant_id

# Each criterion as a recruiter might type it: once in the rubric's words, once not.
QUERIES: dict[str, list[str]] = {
    "Python backend services": ["Python backend services", "who has built APIs in Python?"],
    "Payment processing": ["payment processing", "settlement and card payments experience"],
    "Kubernetes in production": ["Kubernetes in production", "runs container orchestration"],
}
ABSOLUTE = [0.0, 0.2, 0.3, 0.4, 0.5, 0.6, 0.7]
RELATIVE = [0.0, 0.7, 0.8, 0.85, 0.9, 0.95]


def _cos(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / ((math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))) or 1.0)


def _prf(returned: set[str], relevant: set[str]) -> tuple[float, float, float]:
    hit = len(returned & relevant)
    p = hit / len(returned) if returned else 1.0
    r = hit / len(relevant) if relevant else 1.0
    return p, r, (2 * p * r / (p + r) if p + r else 0.0)


async def _measure(embedder: Embedder) -> dict[str, float]:
    cohort = build_cohort(50)
    parser = PyMuPdfParser()
    chunker = StructureAwareChunker()
    vectors: dict[str, list[list[float]]] = {}
    for applicant in cohort:
        parsed = await parser.parse(applicant.pdf, media_type=MediaType.PDF)
        chunks = chunker.chunk(
            parsed,
            tenant_id=new_tenant_id(),
            document_version_id=new_document_version_id(),
            document_title=applicant.filename,
        )
        vectors[applicant.name] = await embedder.embed_documents([c.embedding_text for c in chunks])

    rows: dict[tuple[str, float], list[float]] = {}
    for index, (criterion, _, _) in enumerate(BLOCKS):
        relevant = {a.name for a in cohort if a.has[index]}
        for query in QUERIES[criterion]:
            q = await embedder.embed_query(query)
            scores = {name: max(_cos(q, v) for v in vs) for name, vs in vectors.items()}
            best = max(scores.values())
            for floor in ABSOLUTE:
                rows.setdefault(("absolute", floor), []).append(
                    _prf({n for n, s in scores.items() if s >= floor}, relevant)[2]
                )
            for fraction in RELATIVE:
                rows.setdefault(("relative", fraction), []).append(
                    _prf({n for n, s in scores.items() if s >= best * fraction}, relevant)[2]
                )
            ranked = sorted(scores.values(), reverse=True)
            print(
                f"  {query[:38]:<40} best {best:.3f}  median {statistics.median(ranked):.3f}  "
                f"relevant {len(relevant)}/50"
            )

    print(f"\n  {'cutoff':<22}{'mean F1 over 6 queries':>24}")
    best_row = max(rows, key=lambda k: statistics.fmean(rows[k]))
    for key, f1s in rows.items():
        mark = "  <- best" if key == best_row else ""
        print(f"  {key[0]:<10}{key[1]:<12}{statistics.fmean(f1s):>24.3f}{mark}")
    return {f"{kind}:{value}": statistics.fmean(f1s) for (kind, value), f1s in rows.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="insightlm-eval-talent", description=__doc__)
    parser.add_argument("--embedder", choices=["offline", "gemini"], default="offline")
    parser.add_argument("--out", type=Path, help="Write mean F1 per cutoff as JSON.")
    args = parser.parse_args(argv)
    settings = get_settings()
    if args.embedder == "gemini":
        if settings.gemini_api_key is None:
            parser.error("--embedder gemini needs GEMINI_API_KEY")
        inner = _build_embedder(settings)
    else:
        inner = HashingEmbedder()
    embedder = CachingEmbedder(inner, EmbeddingStore(Path("eval/.cache/embeddings.sqlite")))
    print(f"talent search on cohort-v1, embedder {inner.model_id}\n")
    f1 = asyncio.run(_measure(embedder))
    if args.out:
        args.out.write_text(json.dumps({"embedder": inner.model_id, "mean_f1": f1}, indent=2))
    print(f"\nembedding calls this run: {embedder.misses} texts (rest from cache)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
