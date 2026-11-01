"""Model calls, observed: spans, latency, tokens and cost per workspace.

Wrappers applied in the composition root, so no adapter imports telemetry and every
provider is measured the same way. Each call is a span named for what it does
(``gen_ai.judge``, ``gen_ai.generate``, ``gen_ai.embed``) carrying the model and token
counts, as a child of whatever request or ingestion step triggered it.

Cost is estimated from list prices per million tokens. It's for spotting which
workspace and which step dominates spend, not for invoicing.
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from typing import Any

from opentelemetry.trace import Span, Status, StatusCode

from insightlm.recruiting.domain.rubric import Criterion
from insightlm.recruiting.ports.judge import CriterionJudge, JudgeResult
from insightlm.retrieval.domain.evidence import EvidenceBundle
from insightlm.retrieval.ports.generation import (
    ClaimProposed,
    GenerationEvent,
    GenerationFinished,
    GenerationResult,
    GroundedGenerator,
)
from insightlm.retrieval.ports.indexing import Embedder
from insightlm.shared.observability.telemetry import (
    MODEL_CALLS,
    MODEL_COST,
    MODEL_LATENCY,
    MODEL_TOKENS,
    current_tenant,
    tracer,
)

# USD per million tokens (input, output), list prices at the time of writing.
PRICES: dict[str, tuple[float, float]] = {
    "gemini-2.5-pro": (1.25, 10.00),
    "gemini-2.5-flash": (0.30, 2.50),
    "gemini-embedding-001": (0.15, 0.0),
    "claude-opus-5": (5.00, 25.00),
}


@contextmanager
def _observed(kind: str, model: str, **attributes: Any) -> Iterator[Span]:
    labels = {"kind": kind, "model": model, "tenant": current_tenant()}
    started = time.perf_counter()
    with tracer.start_as_current_span(f"gen_ai.{kind}") as span:
        span.set_attribute("gen_ai.request.model", model)
        span.set_attribute("insightlm.tenant", labels["tenant"])
        for key, value in attributes.items():
            span.set_attribute(key, value)
        try:
            yield span
            labels["outcome"] = "ok"
        except Exception as error:
            labels["outcome"] = type(error).__name__
            span.set_status(Status(StatusCode.ERROR, str(error)))
            raise
        finally:
            MODEL_CALLS.add(1, labels)
            MODEL_LATENCY.record((time.perf_counter() - started) * 1000, labels)


def _usage(span: Span, kind: str, model: str, input_tokens: int, output_tokens: int) -> None:
    span.set_attribute("gen_ai.usage.input_tokens", input_tokens)
    span.set_attribute("gen_ai.usage.output_tokens", output_tokens)
    labels = {"kind": kind, "model": model, "tenant": current_tenant()}
    MODEL_TOKENS.add(input_tokens, {**labels, "direction": "input"})
    MODEL_TOKENS.add(output_tokens, {**labels, "direction": "output"})
    rate_in, rate_out = PRICES.get(model, (0.0, 0.0))
    MODEL_COST.add((input_tokens * rate_in + output_tokens * rate_out) / 1_000_000, labels)


class ObservedJudge:
    def __init__(self, inner: CriterionJudge, model: str) -> None:
        self._inner = inner
        self._model = model

    async def judge(self, criterion: Criterion, evidence: EvidenceBundle) -> JudgeResult:
        with _observed("judge", self._model, **{"insightlm.passages": len(evidence.spans)}) as span:
            result = await self._inner.judge(criterion, evidence)
            span.set_attribute("insightlm.verdict", result.verdict.value)
            _usage(span, "judge", self._model, result.input_tokens, result.output_tokens)
            return result


class ObservedGenerator:
    def __init__(self, inner: GroundedGenerator, model: str) -> None:
        self._inner = inner
        self._model = model

    async def generate(
        self, question: str, evidence: EvidenceBundle, *, feedback: str | None = None
    ) -> GenerationResult:
        with _observed(
            "generate", self._model, **{"insightlm.retry": feedback is not None}
        ) as span:
            result = await self._inner.generate(question, evidence, feedback=feedback)
            span.set_attribute("insightlm.refused", result.refused)
            _usage(span, "generate", self._model, result.input_tokens, result.output_tokens)
            return result

    async def stream(
        self, question: str, evidence: EvidenceBundle, *, feedback: str | None = None
    ) -> AsyncIterator[GenerationEvent]:
        """Streams pass through untouched; usage is recorded when the stream finishes,
        so a streamed answer costs the same on the dashboard as a buffered one."""
        native = getattr(self._inner, "stream", None)
        if native is None:
            result = await self.generate(question, evidence, feedback=feedback)
            if not result.refused:
                for claim in result.claims:
                    yield ClaimProposed(claim)
            yield GenerationFinished(result)
            return
        with _observed(
            "generate",
            self._model,
            **{"insightlm.retry": feedback is not None, "insightlm.stream": True},
        ) as span:
            async for event in native(question, evidence, feedback=feedback):
                if isinstance(event, GenerationFinished):
                    result = event.result
                    span.set_attribute("insightlm.refused", result.refused)
                    _usage(span, "generate", self._model, result.input_tokens, result.output_tokens)
                yield event


class ObservedEmbedder:
    """Counted as calls and latency only: these SDKs report no token usage for
    embeddings, and an invented token count would be worse than none."""

    def __init__(self, inner: Embedder) -> None:
        self._inner = inner

    @property
    def dimensions(self) -> int:
        return self._inner.dimensions

    @property
    def model_id(self) -> str:
        return self._inner.model_id

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        with _observed("embed", self.model_id, **{"insightlm.texts": len(texts)}):
            return await self._inner.embed_documents(texts)

    async def embed_query(self, text: str) -> list[float]:
        with _observed("embed", self.model_id, **{"insightlm.texts": 1}):
            return await self._inner.embed_query(text)
