"""The citation contract, enforced.

> Every AI-generated claim is a tuple ``(text, citation_ids)`` where each citation
> resolves to a persisted span. A claim that fails resolution is not shown to the user.

This module is where that sentence becomes code. The model proposes; :func:`validate`
disposes. Nothing reaches a reader without passing through here.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from insightlm.retrieval.domain.evidence import EvidenceBundle, RetrievedSpan
from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.domain.ids import ChunkId, DocumentId


class AnswerOutcome(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    """Not a failure. A grounded system that says "the corpus does not support an
    answer" is behaving correctly, and the Phase 2 golden set measures it as such."""
    VALIDATION_FAILED = "validation_failed"
    """The model produced claims we could not tie to evidence, twice. We show the
    evidence instead of prose rather than show something unsupported."""


@dataclass(frozen=True, slots=True)
class ProposedClaim:
    """What the model returned, before validation. Citations are unverified labels."""

    text: str
    citation_labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Citation:
    """A resolved reference: enough for the viewer to scroll and highlight."""

    label: str
    chunk_id: ChunkId
    document_id: DocumentId
    document_title: str
    page: int
    char_start: int
    char_end: int
    quoted_text: str

    @classmethod
    def from_span(cls, span: RetrievedSpan) -> Citation:
        return cls(
            label=span.label,
            chunk_id=span.chunk_id,
            document_id=span.document_id,
            document_title=span.document_title,
            page=span.page,
            char_start=span.span.char_start,
            char_end=span.span.char_end,
            quoted_text=span.text,
        )


@dataclass(frozen=True, slots=True)
class Claim:
    """A validated claim. Every citation resolved."""

    text: str
    citations: tuple[Citation, ...]

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValidationError("a claim must say something")
        if not self.citations:
            raise ValidationError("a claim without a citation cannot exist")


@dataclass(frozen=True, slots=True)
class ValidationReport:
    """Why validation failed, in enough detail to feed back to the model on retry."""

    unknown_labels: tuple[str, ...] = ()
    uncited_claims: tuple[str, ...] = ()

    @property
    def is_valid(self) -> bool:
        return not self.unknown_labels and not self.uncited_claims

    def as_feedback(self) -> str:
        parts: list[str] = []
        if self.unknown_labels:
            parts.append(
                "These citation ids do not exist in the provided sources: "
                + ", ".join(self.unknown_labels)
                + ". Cite only the ids given to you."
            )
        if self.uncited_claims:
            parts.append(
                f"{len(self.uncited_claims)} statement(s) carried no citation. "
                "Every statement must cite at least one source id."
            )
        return " ".join(parts)


@dataclass(frozen=True, slots=True)
class GroundedAnswer:
    """The only shape an answer is allowed to take."""

    outcome: AnswerOutcome
    claims: tuple[Claim, ...]
    evidence: EvidenceBundle
    model_version: str
    prompt_version: str
    refusal_reason: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cached_input_tokens: int = 0

    @property
    def text(self) -> str:
        """The prose, rendered from validated claims rather than stored as prose.

        Deliberate: the claims are the data, the paragraph is a view of them. It is
        also what makes the same answer re-renderable with inline markers, as a table,
        or per-criterion in Phase 4.
        """
        return " ".join(claim.text for claim in self.claims)

    @property
    def citations(self) -> tuple[Citation, ...]:
        seen: dict[str, Citation] = {}
        for claim in self.claims:
            for citation in claim.citations:
                seen.setdefault(citation.label, citation)
        return tuple(seen.values())


def validate(
    proposed: list[ProposedClaim], evidence: EvidenceBundle
) -> tuple[tuple[Claim, ...], ValidationReport]:
    """Resolve every proposed citation against the evidence actually retrieved.

    Two failure modes, deliberately distinguished because they mean different things:

    * **unknown label** -- the model invented a source id. It is confabulating a
      reference, which is the exact failure the citation contract exists to catch.
    * **uncited claim** -- the model asserted something with no source at all. The
      statement may even be true, but we cannot show why, so we will not show it.

    Claims that fail are dropped, not repaired. A partially-grounded answer with the
    bad parts silently removed would still be an answer the user cannot audit, so the
    caller retries once with :meth:`ValidationReport.as_feedback` and then degrades to
    showing the evidence alone.
    """
    available = evidence.by_label()
    validated: list[Claim] = []
    unknown: list[str] = []
    uncited: list[str] = []

    for claim in proposed:
        if not claim.text.strip():
            continue
        if not claim.citation_labels:
            uncited.append(claim.text)
            continue

        resolved: list[Citation] = []
        for label in claim.citation_labels:
            span = available.get(label.strip().upper())
            if span is None:
                unknown.append(label)
            else:
                resolved.append(Citation.from_span(span))

        if resolved:
            validated.append(Claim(text=claim.text.strip(), citations=tuple(resolved)))
        else:
            uncited.append(claim.text)

    return tuple(validated), ValidationReport(
        unknown_labels=tuple(dict.fromkeys(unknown)),
        uncited_claims=tuple(uncited),
    )
