"""A deterministic, offline grounded generator.

It does not call a model. It selects the sentences from the retrieved evidence that
best overlap the question, and cites the span each came from. Because every claim is
literally copied from a source, it is grounded by construction -- it cannot
hallucinate, and it cannot cite something it did not read.

Two jobs:

**CI and local development without a key.** The entire ingest -> retrieve -> answer ->
validate -> highlight path runs offline and deterministically, so tests assert exact
output rather than "something plausible came back".

**The extractive baseline in the Phase 2 ablation.** Its ceiling is real and
interesting: it can only return sentences that already exist, so it cannot synthesise
across sources, cannot answer a comparative question, and cannot paraphrase. Measuring
how much the generative configurations beat it -- and on which question types -- is
part of answering RQ1.
"""

from __future__ import annotations

import re

from insightlm.retrieval.domain.answer import ProposedClaim
from insightlm.retrieval.domain.evidence import EvidenceBundle
from insightlm.retrieval.ports.generation import GenerationResult

_SENTENCE = re.compile(r"(?<=[.!?])\s+|\n+")
_WORD = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
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
        "how",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
    ]
)

MODEL_ID = "extractive-overlap-v1"
PROMPT_ID = "extractive@none"


class ExtractiveGenerator:
    def __init__(self, *, max_claims: int = 4, min_overlap: int = 1) -> None:
        self._max_claims = max_claims
        self._min_overlap = min_overlap

    async def generate(
        self,
        question: str,
        evidence: EvidenceBundle,
        *,
        feedback: str | None = None,
    ) -> GenerationResult:
        # `feedback` is ignored on purpose: this generator cannot produce an invalid
        # citation, so there is never anything to correct.
        if evidence.is_empty:
            return GenerationResult(
                refused=True,
                refusal_reason="No relevant passages were found in the corpus.",
                model_version=MODEL_ID,
                prompt_version=PROMPT_ID,
            )

        terms = self._terms(question)
        scored: list[tuple[float, str, str]] = []

        for span in evidence.spans:
            for sentence in self._sentences(span.text):
                overlap = len(terms & self._terms(sentence))
                if overlap < self._min_overlap:
                    continue
                # Retrieval rank breaks ties, so a sentence from a better-matching
                # chunk wins over an equally-overlapping one from a worse chunk.
                score = overlap + span.score * 0.01
                scored.append((score, sentence, span.label))

        if not scored:
            return GenerationResult(
                refused=True,
                refusal_reason=(
                    "The retrieved passages do not contain wording that answers this question."
                ),
                model_version=MODEL_ID,
                prompt_version=PROMPT_ID,
            )

        scored.sort(key=lambda item: (-item[0], item[1]))

        claims: list[ProposedClaim] = []
        seen: set[str] = set()
        for _, sentence, label in scored:
            normalised = sentence.lower()
            if normalised in seen:
                continue
            seen.add(normalised)
            claims.append(ProposedClaim(text=sentence, citation_labels=(label,)))
            if len(claims) >= self._max_claims:
                break

        return GenerationResult(
            claims=claims,
            model_version=MODEL_ID,
            prompt_version=PROMPT_ID,
        )

    def _terms(self, text: str) -> set[str]:
        return {
            word for word in _WORD.findall(text.lower()) if word not in _STOPWORDS and len(word) > 2
        }

    def _sentences(self, text: str) -> list[str]:
        return [
            cleaned for raw in _SENTENCE.split(text) if len(cleaned := " ".join(raw.split())) > 15
        ]
