"""The model-call wrappers change nothing about the call: same result, same errors."""

from __future__ import annotations

import pytest

from insightlm.instrumented import ObservedEmbedder, ObservedGenerator, ObservedJudge
from insightlm.recruiting.adapters.judges import OfflineCriterionJudge
from insightlm.retrieval.adapters.embedding import HashingEmbedder
from insightlm.retrieval.adapters.extractive_generator import ExtractiveGenerator
from insightlm.shared.domain.errors import UpstreamError
from tests.unit.test_citation_contract import bundle, span
from tests.unit.test_recruiting_domain import criterion


async def test_an_observed_judge_returns_exactly_what_the_judge_returned() -> None:
    evidence = bundle(span("C1", "Operated Kubernetes clusters in production."))
    inner = await OfflineCriterionJudge().judge(criterion("Kubernetes in production"), evidence)
    observed = await ObservedJudge(OfflineCriterionJudge(), "m").judge(
        criterion("Kubernetes in production"), evidence
    )
    assert (observed.verdict, observed.citation_labels) == (inner.verdict, inner.citation_labels)


async def test_an_observed_judge_lets_failures_through_unchanged() -> None:
    class Broken:
        async def judge(self, *_: object) -> object:
            raise UpstreamError("no parsable verdict")

    with pytest.raises(UpstreamError):
        await ObservedJudge(Broken(), "m").judge(criterion("x"), bundle(span("C1", "x")))  # type: ignore[arg-type]


async def test_observed_generator_and_embedder_are_transparent() -> None:
    evidence = bundle(span("C1", "Built settlement services in Python."))
    result = await ObservedGenerator(ExtractiveGenerator(), "m").generate("Python?", evidence)
    assert result.claims

    inner = HashingEmbedder()
    embedder = ObservedEmbedder(inner)
    assert embedder.model_id == inner.model_id and embedder.dimensions == inner.dimensions
    assert await embedder.embed_query("python") == await inner.embed_query("python")
    assert await embedder.embed_documents(["a", "b"]) == await inner.embed_documents(["a", "b"])
