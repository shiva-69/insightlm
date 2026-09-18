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

from typing import Any

from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from insightlm.retrieval.adapters.prompt_registry import load_prompt
from insightlm.retrieval.domain.answer import ProposedClaim
from insightlm.retrieval.domain.evidence import EvidenceBundle
from insightlm.retrieval.ports.generation import GenerationResult
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
    ) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._max_output_tokens = max_output_tokens
        self._prompt = load_prompt(PROMPT_NAME)

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError)),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=1.0, max=20.0),
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
            return GenerationResult(
                refused=True,
                refusal_reason="No relevant passages were found in the corpus.",
                model_version=self._model,
                prompt_version=self._prompt.version_id,
            )

        content = self._prompt.render(sources=evidence.as_context(), question=question.strip())
        if feedback:
            content += (
                "\n\n## Correction\n\nYour previous response was rejected. "
                f"{feedback} Produce a corrected answer."
            )

        from google.genai import types

        response = await self._client.aio.models.generate_content(
            model=self._model,
            contents=content,
            config=types.GenerateContentConfig(
                system_instruction=(
                    "You are a retrieval-grounded analyst. You answer only from "
                    "supplied sources and you cite every claim."
                ),
                response_mime_type="application/json",
                response_schema=_RESPONSE_SCHEMA,
                max_output_tokens=self._max_output_tokens,
            ),
        )

        usage = response.usage_metadata
        input_tokens = getattr(usage, "prompt_token_count", 0) or 0
        output_tokens = getattr(usage, "candidates_token_count", 0) or 0
        cached_tokens = getattr(usage, "cached_content_token_count", 0) or 0

        parsed = response.parsed
        if not isinstance(parsed, dict):
            _logger.error("structured_output_missing", provider="gemini")
            return GenerationResult(
                refused=True,
                refusal_reason="The model did not return a usable response.",
                model_version=self._model,
                prompt_version=self._prompt.version_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_tokens,
            )

        if parsed.get("suspicious_content"):
            _logger.warning(
                "suspicious_source_content",
                provider="gemini",
                detail=str(parsed["suspicious_content"])[:300],
            )

        if not parsed.get("sufficient_evidence"):
            return GenerationResult(
                refused=True,
                refusal_reason=str(
                    parsed.get("missing_information")
                    or "The supplied documents do not answer this question."
                ),
                model_version=self._model,
                prompt_version=self._prompt.version_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cached_input_tokens=cached_tokens,
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

        return GenerationResult(
            claims=claims,
            model_version=self._model,
            prompt_version=self._prompt.version_id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=cached_tokens,
        )
