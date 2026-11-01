"""Playbooks: the positions a contract should take, approved by counsel before use.

The same rule recruiting turns on, in a different domain: **a model never defines the
standard a document is judged against.** A playbook can be drafted by anyone, but no
contract can be reviewed against it until someone with approval rights -- not its author
-- has approved it. Approved playbooks are immutable; changing one creates a new version,
and every review records the version it was run under.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Self

from insightlm.contracts.domain.ids import PlaybookId, PositionId
from insightlm.shared.domain.errors import ConflictError, ValidationError
from insightlm.shared.domain.ids import TenantId, UserId


class Severity(StrEnum):
    """How much a gap in this position matters. Decides who must look at the review."""

    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"


@dataclass(frozen=True, slots=True)
class Position:
    """One clause the contract should contain, and what acceptable looks like."""

    id: PositionId
    clause: str
    """The clause, as a lawyer would name it: "Limitation of liability"."""
    standard: str
    """The acceptable position: "Liability capped at 12 months' fees; no cap on
    confidentiality breaches". The judge decides whether the contract meets it."""
    severity: Severity
    evidence_hint: str = ""
    """Words the clause tends to use, to phrase the search: "liability, cap, aggregate"."""

    def __post_init__(self) -> None:
        if not self.clause.strip():
            raise ValidationError("a position needs a clause")
        if not self.standard.strip():
            raise ValidationError("a position needs a standard to be judged against")

    @property
    def query(self) -> str:
        return f"{self.clause}. {self.evidence_hint}".strip()


class PlaybookStatus(StrEnum):
    DRAFT = "draft"
    APPROVED = "approved"
    SUPERSEDED = "superseded"


@dataclass(frozen=True, slots=True)
class Playbook:
    id: PlaybookId
    tenant_id: TenantId
    name: str
    version: int
    positions: tuple[Position, ...]
    proposed_by: UserId
    created_at: datetime
    status: PlaybookStatus = PlaybookStatus.DRAFT
    approved_by: UserId | None = None
    approved_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValidationError("a playbook needs a name")
        if not self.positions:
            raise ValidationError("a playbook needs at least one position")
        clauses = [p.clause.strip().lower() for p in self.positions]
        if len(set(clauses)) != len(clauses):
            raise ValidationError("a playbook cannot take two positions on the same clause")
        if self.status is PlaybookStatus.APPROVED and (
            self.approved_by is None or self.approved_at is None
        ):
            raise ValidationError("an approved playbook must record who approved it and when")

    @property
    def is_approved(self) -> bool:
        return self.status is PlaybookStatus.APPROVED

    def approve(self, *, by: UserId, at: datetime, may_self_approve: bool = False) -> Self:
        """Counsel approve; the author does not, unless they are the workspace owner and
        there is nobody else to ask."""
        if self.status is not PlaybookStatus.DRAFT:
            raise ConflictError(
                f"only a draft playbook can be approved (this one is {self.status})"
            )
        if by == self.proposed_by and not may_self_approve:
            raise ConflictError("a playbook cannot be approved by the person who proposed it")
        return replace(self, status=PlaybookStatus.APPROVED, approved_by=by, approved_at=at)

    def superseded(self) -> Self:
        return replace(self, status=PlaybookStatus.SUPERSEDED)
