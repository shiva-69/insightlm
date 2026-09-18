"""Criterion judges: offline, Gemini, and Claude.

All three satisfy one port, so the choice changes nothing downstream. Whatever a judge
returns, the use case resolves its citation labels against the evidence actually
retrieved and downgrades any finding that cites nothing real -- so a judge cannot
smuggle an unsupported verdict into an assessment, whichever provider it is.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from insightlm.recruiting.domain.assessment import Verdict
from insightlm.recruiting.domain.rubric import Criterion
from insightlm.recruiting.ports.judge import JudgeResult
from insightlm.retrieval.domain.evidence import EvidenceBundle
from insightlm.shared.adapters.prompts import load_prompt_from
from insightlm.shared.adapters.providers import TransientProviderError, classify_gemini_error
from insightlm.shared.domain.errors import UpstreamError

PROMPT_DIR = Path(__file__).parent / "prompts"
PROMPT_NAME = "criterion_judgment.v1"

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "have",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
        "experience",
        "experienced",
        "demonstrated",
        "ability",
        "strong",
        "good",
        "knowledge",
    ]
)


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def _render(criterion: Criterion, evidence: EvidenceBundle) -> str:
    return load_prompt_from(PROMPT_DIR, PROMPT_NAME).render(
        criterion=criterion.label,
        kind=criterion.kind.value.replace("_", " "),
        hint=criterion.evidence_hint or "(none given)",
        sources=evidence.as_context() or "(no sources were found)",
    )


class OfflineCriterionJudge:
    """Deterministic, free, and deliberately crude.

    Scores term overlap between the criterion and each retrieved passage. It cannot
    read a negation and cannot tell depth from mention -- which is exactly why it is
    useful: it is the baseline the model-backed judges must beat, and it lets every
    test and every demo run with no key.
    """

    MODEL_ID = "overlap-judge-v1"

    async def judge(self, criterion: Criterion, evidence: EvidenceBundle) -> JudgeResult:
        wanted = _terms(criterion.query)
        best_label, best_ratio = None, 0.0
        for span in evidence.spans:
            if not wanted:
                break
            ratio = len(wanted & _terms(span.text)) / len(wanted)
            if ratio > best_ratio:
                best_label, best_ratio = span.label, ratio

        if best_label is None or best_ratio < 0.15:
            return JudgeResult(
                verdict=Verdict.ABSENT,
                confidence=0.6,
                rationale="No retrieved passage shares the criterion's key terms.",
                citation_labels=(),
                model_version=self.MODEL_ID,
                prompt_version="overlap@none",
            )

        verdict = (
            Verdict.STRONG
            if best_ratio >= 0.5
            else Verdict.ADEQUATE
            if best_ratio >= 0.3
            else Verdict.WEAK
        )
        return JudgeResult(
            verdict=verdict,
            confidence=round(min(0.5 + best_ratio / 2, 0.9), 2),
            rationale=f"{best_ratio:.0%} of the criterion's key terms appear in {best_label}.",
            citation_labels=(best_label,),
            model_version=self.MODEL_ID,
            prompt_version="overlap@none",
        )


class _Judgment(BaseModel):
    verdict: Literal["strong", "adequate", "weak", "absent"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(description="One sentence explaining the verdict.")
    citations: list[str] = Field(default_factory=list)


_GEMINI_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["strong", "adequate", "weak", "absent"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["verdict", "confidence", "rationale", "citations"],
}


def _absent_without_evidence(model: str, prompt: str) -> JudgeResult:
    # Nothing was retrieved, so there is nothing to judge: skip the paid call.
    return JudgeResult(
        verdict=Verdict.ABSENT,
        confidence=0.9,
        rationale="Retrieval found no passage in the candidate's documents for this criterion.",
        citation_labels=(),
        model_version=model,
        prompt_version=prompt,
    )


_THINKING_BUDGET = 1_024


class GeminiCriterionJudge:
    def __init__(self, api_key: str, *, model: str = "gemini-2.5-pro") -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._prompt = load_prompt_from(PROMPT_DIR, PROMPT_NAME)

    @retry(
        retry=retry_if_exception_type((TimeoutError, ConnectionError, TransientProviderError)),
        stop=stop_after_attempt(4),
        wait=wait_exponential_jitter(initial=1.0, max=30.0),
        reraise=True,
    )
    async def judge(self, criterion: Criterion, evidence: EvidenceBundle) -> JudgeResult:
        if evidence.is_empty:
            return _absent_without_evidence(self._model, self._prompt.version_id)

        from google.genai import types

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=_render(criterion, evidence),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_GEMINI_SCHEMA,
                    # gemini-2.5-pro thinks before it answers, and thinking counts against
                    # max_output_tokens. At a 2k ceiling a longer think occasionally left
                    # no room for the verdict at all. Thinking is capped, and the ceiling
                    # sits well above cap + answer.
                    thinking_config=types.ThinkingConfig(thinking_budget=_THINKING_BUDGET),
                    max_output_tokens=_THINKING_BUDGET + 2_000,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except Exception as error:
            raise classify_gemini_error(error) from error

        if response.parsed is None:
            candidates = response.candidates or []
            raise UpstreamError(
                "the judge returned no parsable verdict",
                detail={
                    "model": self._model,
                    "criterion": criterion.label,
                    "finish_reason": str(candidates[0].finish_reason) if candidates else None,
                },
            )
        try:
            parsed = _Judgment.model_validate(response.parsed)
        except ValidationError as error:
            # A malformed verdict is an error, never a default. Falling back to
            # "absent" here would quietly mark a candidate as lacking something.
            raise UpstreamError(
                "the judge returned a malformed verdict",
                detail={"model": self._model, "criterion": criterion.label, "error": str(error)},
            ) from error
        usage = response.usage_metadata
        return JudgeResult(
            verdict=Verdict(parsed.verdict),
            confidence=parsed.confidence,
            rationale=parsed.rationale,
            citation_labels=tuple(parsed.citations),
            model_version=self._model,
            prompt_version=self._prompt.version_id,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
        )


class AnthropicCriterionJudge:
    def __init__(self, api_key: str, *, model: str = "claude-opus-5") -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._prompt = load_prompt_from(PROMPT_DIR, PROMPT_NAME)

    async def judge(self, criterion: Criterion, evidence: EvidenceBundle) -> JudgeResult:
        if evidence.is_empty:
            return _absent_without_evidence(self._model, self._prompt.version_id)

        response = await self._client.messages.parse(
            model=self._model,
            max_tokens=4_000,
            messages=[{"role": "user", "content": _render(criterion, evidence)}],
            # A narrow classification over supplied text, not a reasoning problem.
            output_config={"effort": "low"},
            output_format=_Judgment,
        )
        parsed = response.parsed_output
        if response.stop_reason == "refusal" or parsed is None:
            raise UpstreamError(
                "the judge returned no usable verdict",
                detail={"model": self._model, "stop_reason": str(response.stop_reason)},
            )
        return JudgeResult(
            verdict=Verdict(parsed.verdict),
            confidence=parsed.confidence,
            rationale=parsed.rationale,
            citation_labels=tuple(parsed.citations),
            model_version=self._model,
            prompt_version=self._prompt.version_id,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
        )
