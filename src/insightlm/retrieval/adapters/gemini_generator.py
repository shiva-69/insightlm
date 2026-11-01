"""Grounded generation via the Gemini API.

The same contract as the Anthropic adapter, and for the same reasons: schema-
constrained output so claims and citation ids arrive as data, opaque `C1` tags so the
model never has to reproduce a document title it will get subtly wrong, and refusal
as a first-class outcome.

Having two providers behind one port is not only about choice. The dissertation's
RQ2/RQ3 ask what each pipeline stage contributes and what it costs; being able to
re-run the same ablation on a second model turns "our pipeline scores X" into
"the effect holds across models", which is a materially stronger claim. Validation is
identical either way because it lives in the domain, not in the adapter.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from dataclasses import replace
from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from insightlm.retrieval.adapters.claim_stream import ClaimStreamParser
from insightlm.retrieval.adapters.prompt_registry import load_prompt
from insightlm.retrieval.domain.answer import ProposedClaim
from insightlm.retrieval.domain.evidence import EvidenceBundle
from insightlm.retrieval.ports.generation import (
    ClaimProposed,
    GenerationEvent,
    GenerationFinished,
    GenerationResult,
)
from insightlm.shared.adapters.providers import (
    TransientProviderError,
    classify_gemini_error,
)
from insightlm.shared.observability.logging import get_logger

_logger = get_logger(__name__)

PROMPT_NAME = "grounded_answer.v1"
DEFAULT_MODEL = "gemini-2.5-pro"


# The response schema, in the shape Gemini expects. It mirrors the Pydantic model the
# Anthropic adapter constrains against -- both exist to produce the same domain type.
_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sufficient_evidence": {
            "type": "boolean",
            "description": "False when the sources do not support an answer.",
        },
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "citations": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["text", "citations"],
            },
        },
        "missing_information": {"type": "string"},
        "suspicious_content": {"type": "string"},
    },
    "required": ["sufficient_evidence", "claims"],
}


class GeminiGroundedGenerator:
    def __init__(
        self,
        api_key: str,
        *,
        model: str = DEFAULT_MODEL,
        max_output_tokens: int = 8_000,
        thinking_budget: int | None = None,
    ) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._max_output_tokens = max_output_tokens
        # None leaves the model's default. Flash models accept 0 (no thinking), which
        # is what makes them fast; Pro models always think, so they are left alone.
        self._thinking_budget = thinking_budget
        self._prompt = load_prompt(PROMPT_NAME)

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError, TransientProviderError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(initial=1.0, max=30.0),
        reraise=True,
    )
    async def generate(
        self,
        question: str,
        evidence: EvidenceBundle,
        *,
        feedback: str | None = None,
    ) -> GenerationResult:
        if evidence.is_empty:
            return self._empty()

        content, config = self._request(question, evidence, feedback)
        try:
            response = await self._client.aio.models.generate_content(
                model=self._model, contents=content, config=config
            )
        except Exception as error:
            raise classify_gemini_error(error) from error
        parsed = response.parsed
        return self._result(parsed if isinstance(parsed, dict) else None, response.usage_metadata)

    async def stream(
        self,
        question: str,
        evidence: EvidenceBundle,
        *,
        feedback: str | None = None,
    ) -> AsyncIterator[GenerationEvent]:
        """The same request, streamed: each claim is released when its object closes.

        Not retried on a transient error the way ``generate`` is -- claims may already
        be on the reader's screen -- so the error propagates and the caller reports it.
        """
        if evidence.is_empty:
            yield GenerationFinished(self._empty())
            return
        content, config = self._request(question, evidence, feedback)
        parser = ClaimStreamParser()
        usage: Any = None
        try:
            chunks = await self._client.aio.models.generate_content_stream(
                model=self._model, contents=content, config=config
            )
            async for chunk in chunks:
                if chunk.usage_metadata is not None:
                    usage = chunk.usage_metadata
                for claim in parser.feed(_chunk_text(chunk)):
                    yield ClaimProposed(claim)
        except Exception as error:
            raise classify_gemini_error(error) from error
        try:
            parsed = json.loads(parser.text)
        except json.JSONDecodeError:
            parsed = None
        yield GenerationFinished(self._result(parsed if isinstance(parsed, dict) else None, usage))

    def _empty(self) -> GenerationResult:
        return GenerationResult(
            refused=True,
            refusal_reason="No relevant passages were found in the corpus.",
            model_version=self._model,
            prompt_version=self._prompt.version_id,
        )

    def _request(
        self, question: str, evidence: EvidenceBundle, feedback: str | None
    ) -> tuple[str, Any]:
        content = self._prompt.render(sources=evidence.as_context(), question=question.strip())
        if feedback:
            content += (
                "\n\n## Correction\n\nYour previous response was rejected. "
                f"{feedback} Produce a corrected answer."
            )

        from google.genai import types

        config = types.GenerateContentConfig(
            system_instruction=(
                "You are a retrieval-grounded analyst. You answer only from "
                "supplied sources and you cite every claim."
            ),
            response_mime_type="application/json",
            response_schema=_RESPONSE_SCHEMA,
            max_output_tokens=self._max_output_tokens,
            # This adapter declares no tools, so the SDK's automatic function
            # calling has nothing to do but warn about itself on every call.
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
            **(
                {"thinking_config": types.ThinkingConfig(thinking_budget=self._thinking_budget)}
                if self._thinking_budget is not None
                else {}
            ),
        )
        return content, config

    def _result(self, parsed: dict[str, Any] | None, usage: Any) -> GenerationResult:
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        cached_tokens = getattr(usage, "cached_content_token_count", 0) or 0
        base = GenerationResult(
            model_version=self._model,
            prompt_version=self._prompt.version_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_tokens,
        )

        if parsed is None:
            _logger.error("structured_output_missing", provider="gemini")
            return replace(
                base, refused=True, refusal_reason="The model did not return a usable response."
            )

        if parsed.get("suspicious_content"):
            _logger.warning(
                "suspicious_source_content",
                provider="gemini",
                detail=str(parsed["suspicious_content"])[:300],
            )

        if not parsed.get("sufficient_evidence"):
            return replace(
                base,
                refused=True,
                refusal_reason=str(
                    parsed.get("missing_information")
                    or "The supplied documents do not answer this question."
                ),
            )

        claims: list[ProposedClaim] = []
        for raw in parsed.get("claims", []):
            if not isinstance(raw, dict):
                continue
            citations = raw.get("citations") or []
            claims.append(
                ProposedClaim(
                    text=str(raw.get("text", "")),
                    citation_labels=tuple(str(label) for label in citations),
                )
            )
        return replace(base, claims=claims)


def _chunk_text(chunk: Any) -> str:
    """A chunk's text, or nothing: a chunk carrying only usage or thoughts has none."""
    try:
        return str(chunk.text or "")
    except (ValueError, AttributeError):
        return ""
