"""Requisitions and rubrics -- and the rule the whole project turns on.

**A model must never define the criteria by which people are judged.** The rubric can
be drafted by anyone, including a model, but it cannot be *used* until a human with
the authority to approve it has done so. That is enforced here, structurally: an
assessment cannot be constructed against an unapproved rubric, and there is no path
around it that a use case could take by accident.

Rubrics are versioned and immutable once approved. Editing an approved rubric forks a
new version; assessments keep pointing at the version they were run under. Otherwise a
shortlist could be silently re-scored against criteria nobody applied at the time,
which is exactly the kind of history an auditor needs to be able to trust.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Self

from insightlm.shared.domain.errors import ConflictError, ValidationError
from insightlm.shared.domain.ids import (
    CriterionId,
    RequisitionId,
    RubricId,
    TenantId,
    UserId,
)


class RequisitionStatus(StrEnum):
    DRAFT = "draft"
    OPEN = "open"
    CLOSED = "closed"


@dataclass(frozen=True, slots=True)
class Requisition:
    id: RequisitionId
    tenant_id: TenantId
    title: str
    description: str
    created_by: UserId
    created_at: datetime
    status: RequisitionStatus = RequisitionStatus.OPEN
    # Judges see a redacted copy of the evidence: no name, contact details,
    # institutions, demographic lines or gendered pronouns. On by default.
    blind_screening: bool = True

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValidationError("a requisition needs a title")


class CriterionKind(StrEnum):
    MUST_HAVE = "must_have"
    STRONG_PREFERENCE = "strong_preference"
    NICE_TO_HAVE = "nice_to_have"


@dataclass(frozen=True, slots=True)
class Criterion:
    id: CriterionId
    label: str
    kind: CriterionKind
    weight: float
    evidence_hint: str = ""
    """What evidence would look like. Used to phrase the retrieval query, so a
    criterion reading "payments depth" searches for settlement, idempotency and
    reconciliation rather than for the literal word "depth"."""
    disqualifying: bool = False
    """Absence of evidence flags the application for a human decision. It never
    rejects anyone by itself -- see Assessment."""

    def __post_init__(self) -> None:
        if not self.label.strip():
            raise ValidationError("a criterion needs a label")
        if not 0.0 < self.weight <= 1.0:
            raise ValidationError(
                "criterion weight must be in (0, 1]", detail={"weight": self.weight}
            )

    @property
    def query(self) -> str:
        return f"{self.label}. {self.evidence_hint}".strip()


class RubricStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class Rubric:
    id: RubricId
    tenant_id: TenantId
    requisition_id: RequisitionId
    version: int
    criteria: tuple[Criterion, ...]
    proposed_by: UserId
    created_at: datetime
    status: RubricStatus = RubricStatus.DRAFT
    approved_by: UserId | None = None
    approved_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.criteria:
            raise ValidationError("a rubric needs at least one criterion")
        labels = [c.label.strip().lower() for c in self.criteria]
        if len(set(labels)) != len(labels):
            raise ValidationError("rubric criteria must have distinct labels")
        if self.status is RubricStatus.APPROVED and (
            self.approved_by is None or self.approved_at is None
        ):
            raise ValidationError("an approved rubric must record who approved it and when")

    @property
    def is_approved(self) -> bool:
        return self.status is RubricStatus.APPROVED

    def approve(self, *, by: UserId, at: datetime, may_self_approve: bool = False) -> Self:
        """Approval by a human is what makes a rubric usable.

        By default the proposer may not approve their own rubric: the person sourcing
        candidates should not also set the bar they are measured against. The one
        exception is the workspace owner, passed in by the use case -- a team of one
        has nobody else to ask, and a rule that makes the product unusable for a
        founder hiring their first engineer is a rule that gets bypassed.
        """
        if self.status is not RubricStatus.DRAFT:
            raise ConflictError(f"only a draft rubric can be approved (this one is {self.status})")
        if by == self.proposed_by and not may_self_approve:
            raise ConflictError("a rubric cannot be approved by the person who proposed it")
        return replace(self, status=RubricStatus.APPROVED, approved_by=by, approved_at=at)

    def superseded(self) -> Self:
        return replace(self, status=RubricStatus.SUPERSEDED)

    def criterion(self, criterion_id: CriterionId) -> Criterion:
        for criterion in self.criteria:
            if criterion.id == criterion_id:
                return criterion
        raise ValidationError("criterion not in this rubric")
