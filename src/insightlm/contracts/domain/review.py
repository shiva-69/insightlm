"""Reviewing a contract against a playbook, position by position.

Two rules carried over from recruiting, because they are about evidence, not hiring:

**The model never decides the outcome.** It answers one narrow question per position --
does this evidence meet this standard? -- with a finding and the passages behind it.
Whether the contract needs counsel's attention is decided here, in code, from those
findings and each position's severity.

**Missing is not the same as deviating.** A clause the search could not find may be
absent, or may be worded unusually or sit in a schedule that was not uploaded. A clause
that is present but takes a worse position is a different problem with a different fix.
They are separate findings, and only one of them can cite anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from insightlm.contracts.domain.ids import ContractReviewId, PlaybookId, PositionId
from insightlm.contracts.domain.playbook import Playbook, Severity
from insightlm.documents.domain.spans import PageRect
from insightlm.shared.domain.errors import InvariantViolationError, ValidationError
from insightlm.shared.domain.ids import DocumentId, TenantId, UserId


class Finding(StrEnum):
    MEETS = "meets"
    """The clause is present and takes the playbook's position, or a better one."""
    DEVIATES = "deviates"
    """The clause is present but departs from the standard."""
    MISSING = "missing"
    """No passage addresses the clause. A statement about the evidence, not a verdict
    that the contract omits it."""


@dataclass(frozen=True, slots=True)
class ClauseEvidence:
    """A cited passage, copied at review time so the review still shows what it rested
    on after the document is replaced."""

    label: str
    chunk_id: str
    document_id: str
    page: int
    char_start: int
    char_end: int
    quoted_text: str
    document_title: str = ""
    rects: tuple[PageRect, ...] = ()


@dataclass(frozen=True, slots=True)
class PositionFinding:
    position_id: PositionId
    finding: Finding
    confidence: float
    rationale: str
    evidence: tuple[ClauseEvidence, ...]

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValidationError("confidence must be in [0, 1]")
        # The citation contract, applied to clauses: saying a clause meets or deviates
        # from the standard is a claim about the text, and must point at the text.
        if self.finding is not Finding.MISSING and not self.evidence:
            raise InvariantViolationError(
                f"a '{self.finding.value}' finding must cite the passage it rests on"
            )


class ReviewFlag(StrEnum):
    CRITICAL_GAP = "critical_gap"
    """A critical position is missing or deviates: counsel must review before signing."""
    MAJOR_GAP = "major_gap"
    LOW_CONFIDENCE = "low_confidence"


LOW_CONFIDENCE = 0.5


@dataclass(frozen=True, slots=True)
class RiskSummary:
    meets: int
    deviates: int
    missing: int
    flags: tuple[ReviewFlag, ...]

    @property
    def needs_counsel(self) -> bool:
        return ReviewFlag.CRITICAL_GAP in self.flags or ReviewFlag.LOW_CONFIDENCE in self.flags


def summarise(playbook: Playbook, findings: list[PositionFinding]) -> RiskSummary:
    """Counts and flags, in code. Deterministic: the same findings under the same
    playbook always give the same summary, so "why was this flagged?" has an answer
    that can be read line by line."""
    if not playbook.is_approved:
        raise InvariantViolationError("cannot summarise against an unapproved playbook")
    by_position = {f.position_id: f for f in findings}
    missing = [p.clause for p in playbook.positions if p.id not in by_position]
    if missing:
        raise InvariantViolationError(
            "every position must be reviewed before summarising", detail={"missing": missing}
        )
    flags: list[ReviewFlag] = []
    for position in playbook.positions:
        finding = by_position[position.id]
        if finding.finding is not Finding.MEETS:
            if position.severity is Severity.CRITICAL and ReviewFlag.CRITICAL_GAP not in flags:
                flags.append(ReviewFlag.CRITICAL_GAP)
            if position.severity is Severity.MAJOR and ReviewFlag.MAJOR_GAP not in flags:
                flags.append(ReviewFlag.MAJOR_GAP)
        if finding.confidence < LOW_CONFIDENCE and ReviewFlag.LOW_CONFIDENCE not in flags:
            flags.append(ReviewFlag.LOW_CONFIDENCE)
    counts = {kind: sum(1 for f in findings if f.finding is kind) for kind in Finding}
    return RiskSummary(
        meets=counts[Finding.MEETS],
        deviates=counts[Finding.DEVIATES],
        missing=counts[Finding.MISSING],
        flags=tuple(flags),
    )


@dataclass(frozen=True, slots=True)
class ContractReview:
    id: ContractReviewId
    tenant_id: TenantId
    title: str
    document_ids: tuple[DocumentId, ...]
    playbook_id: PlaybookId
    playbook_version: int
    findings: tuple[PositionFinding, ...]
    summary: RiskSummary
    reviewed_by: UserId
    created_at: datetime
    model_version: str
    prompt_version: str
    retrieval_config_hash: str

    def __post_init__(self) -> None:
        if not self.document_ids:
            raise ValidationError("a review needs at least one document")
