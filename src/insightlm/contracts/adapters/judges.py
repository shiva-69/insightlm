"""Clause judges: Gemini, and a free offline baseline it must beat."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from insightlm.contracts.domain.playbook import Position
from insightlm.contracts.domain.review import Finding
from insightlm.contracts.ports.judge import ClauseJudgment
from insightlm.retrieval.domain.evidence import EvidenceBundle
from insightlm.shared.adapters.prompts import load_prompt_from
from insightlm.shared.adapters.providers import TransientProviderError, classify_gemini_error
from insightlm.shared.domain.errors import UpstreamError

PROMPT_DIR = Path(__file__).parent / "prompts"
PROMPT_NAME = "clause_review.v1"

_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "any",
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
        "its",
        "may",
        "not",
        "of",
        "on",
        "or",
        "shall",
        "such",
        "that",
        "the",
        "this",
        "to",
        "under",
        "will",
        "with",
        "which",
        "who",
        "party",
        "parties",
        "agreement",
    ]
)
# Words that, near the clause's subject, usually signal a weaker position. Crude on
# purpose: the baseline reads words, not meaning, and the model is measured against it.
_WEAKENING = frozenset(
    ["unlimited", "uncapped", "waived", "waives", "exclusively", "sole", "perpetual", "irrevocable"]
)


def _terms(text: str) -> set[str]:
    return {w for w in _WORD.findall(text.lower()) if w not in _STOP and len(w) > 2}


def _render(position: Position, evidence: EvidenceBundle) -> str:
    return load_prompt_from(PROMPT_DIR, PROMPT_NAME).render(
        clause=position.clause,
        severity=position.severity.value,
        standard=position.standard,
        sources=evidence.as_context() or "(no sources were found)",
    )


def _missing(model: str, prompt_version: str) -> ClauseJudgment:
    return ClauseJudgment(
        finding=Finding.MISSING,
        confidence=0.9,
        rationale="No passage in this contract was retrieved for this clause.",
        citation_labels=(),
        model_version=model,
        prompt_version=prompt_version,
    )


class OfflineClauseJudge:
    """Deterministic, free and deliberately crude.

    MISSING when no passage shares the clause's terms; otherwise MEETS when the passage
    also shares the standard's terms and DEVIATES when it does not, or when it uses a
    weakening word the standard does not. It cannot compare numbers or read who bears an
    obligation, which is the gap the model judge is measured on.
    """

    MODEL_ID = "overlap-clause-judge-v1"

    async def judge(self, position: Position, evidence: EvidenceBundle) -> ClauseJudgment:
        subject = _terms(position.query)
        standard = _terms(position.standard)
        best_label, best_subject, best_text = None, 0.0, ""
        for span in evidence.spans:
            if not subject:
                break
            ratio = len(subject & _terms(span.text)) / len(subject)
            if ratio > best_subject:
                best_label, best_subject, best_text = span.label, ratio, span.text
        if best_label is None or best_subject < 0.2:
            return _missing(self.MODEL_ID, "overlap@none")

        words = _terms(best_text)
        agreement = len(standard & words) / len(standard) if standard else 0.0
        weakened = bool((words & _WEAKENING) - standard)
        finding = Finding.MEETS if agreement >= 0.5 and not weakened else Finding.DEVIATES
        return ClauseJudgment(
            finding=finding,
            confidence=round(min(0.5 + agreement / 2, 0.85), 2),
            rationale=(
                f"{best_label} addresses the clause and shares {agreement:.0%} of the "
                f"standard's terms{'; it uses a weakening term' if weakened else ''}."
            ),
            citation_labels=(best_label,),
            model_version=self.MODEL_ID,
            prompt_version="overlap@none",
        )


class _Judgment(BaseModel):
    finding: Literal["meets", "deviates", "missing"]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str
    citations: list[str] = Field(default_factory=list)


_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "finding": {"type": "string", "enum": ["meets", "deviates", "missing"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "rationale": {"type": "string"},
        "citations": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["finding", "confidence", "rationale", "citations"],
}
_THINKING_BUDGET = 1_024


class GeminiClauseJudge:
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
    async def judge(self, position: Position, evidence: EvidenceBundle) -> ClauseJudgment:
        if evidence.is_empty:
            return _missing(self._model, self._prompt.version_id)

        from google.genai import types

        try:
            response = await self._client.aio.models.generate_content(
                model=self._model,
                contents=_render(position, evidence),
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=_SCHEMA,
                    thinking_config=types.ThinkingConfig(thinking_budget=_THINKING_BUDGET),
                    max_output_tokens=_THINKING_BUDGET + 2_000,
                    automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
                ),
            )
        except Exception as error:
            raise classify_gemini_error(error) from error
        if response.parsed is None:
            raise UpstreamError(
                "the judge returned no parsable finding",
                detail={"model": self._model, "clause": position.clause},
            )
        try:
            parsed = _Judgment.model_validate(response.parsed)
        except ValidationError as error:
            # A malformed finding is an error, never a default: falling back to
            # "missing" would quietly tell counsel a clause is absent.
            raise UpstreamError(
                "the judge returned a malformed finding",
                detail={"model": self._model, "clause": position.clause, "error": str(error)},
            ) from error
        usage = response.usage_metadata
        return ClauseJudgment(
            finding=Finding(parsed.finding),
            confidence=parsed.confidence,
            rationale=parsed.rationale,
            citation_labels=tuple(parsed.citations),
            model_version=self._model,
            prompt_version=self._prompt.version_id,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
        )
