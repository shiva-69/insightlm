"""Tamper-evident audit log.

Every entry commits to its predecessor: ``entry_hash = SHA256(prev_hash || canonical(entry))``.
Mutating any historical row breaks every hash after it, which
:func:`verify_chain` detects. The chain is per-tenant, so one tenant's volume never
serialises another's writes.

Append-only is enforced in three places, deliberately:
  1. no update or delete method exists on the repository port,
  2. a database trigger rejects UPDATE and DELETE for every role, owner included,
  3. this hash chain makes a successful tamper *detectable* even if 1 and 2 fail.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Self

from insightlm.shared.domain.errors import InvariantViolationError
from insightlm.shared.domain.ids import AuditEntryId, TenantId, UserId

GENESIS_HASH = "0" * 64
"""``prev_hash`` of the first entry in a tenant's chain."""


class AuditAction(StrEnum):
    # identity
    TENANT_CREATED = "tenant.created"
    USER_REGISTERED = "user.registered"
    USER_AUTHENTICATED = "user.authenticated"
    INVITATION_CREATED = "invitation.created"
    INVITATION_ACCEPTED = "invitation.accepted"
    INVITATION_REVOKED = "invitation.revoked"
    MEMBERSHIP_ROLE_CHANGED = "membership.role_changed"
    MEMBERSHIP_REVOKED = "membership.revoked"
    # governance
    AUDIT_CHAIN_VERIFIED = "audit.chain_verified"
    # reserved for later phases -- listed here so the vocabulary is designed, not accreted
    DOCUMENT_UPLOADED = "document.uploaded"
    REQUISITION_CREATED = "requisition.created"
    RUBRIC_PROPOSED = "rubric.proposed"
    RUBRIC_APPROVED = "rubric.approved"
    CANDIDATE_ADDED = "candidate.added"
    ASSESSMENT_CREATED = "assessment.created"
    STAGE_TRANSITIONED = "application.stage_transitioned"
    INTERVIEWER_ASSIGNED = "application.interviewer_assigned"
    SCORECARD_SUBMITTED = "scorecard.submitted"
    CANDIDATE_DATA_ACCESSED = "candidate.data_accessed"
    ERASURE_REQUESTED = "erasure.requested"
    ERASURE_COMPLETED = "erasure.completed"


def _canonical(value: object) -> str:
    """Deterministic JSON: sorted keys, no insignificant whitespace, UTF-8.

    Determinism is the whole point -- two runs over the same entry must produce
    byte-identical input to the hash, or verification is meaningless.
    """
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    )


@dataclass(frozen=True, slots=True)
class AuditEntry:
    """One immutable link in a tenant's chain."""

    id: AuditEntryId
    tenant_id: TenantId
    actor_id: UserId | None
    action: AuditAction
    subject_type: str
    subject_id: str | None
    payload: dict[str, object]
    occurred_at: datetime
    prev_hash: str
    entry_hash: str
    model_version: str | None = None
    prompt_version: str | None = None

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise InvariantViolationError("audit entry occurred_at must be timezone-aware")
        if len(self.prev_hash) != 64 or len(self.entry_hash) != 64:
            raise InvariantViolationError("audit hashes must be 64 hex characters")

    @classmethod
    def link(
        cls,
        *,
        entry_id: AuditEntryId,
        tenant_id: TenantId,
        actor_id: UserId | None,
        action: AuditAction,
        subject_type: str,
        subject_id: str | None,
        payload: dict[str, object],
        occurred_at: datetime,
        prev_hash: str,
        model_version: str | None = None,
        prompt_version: str | None = None,
    ) -> Self:
        """Build an entry chained onto ``prev_hash``, computing its own hash."""
        entry_hash = compute_entry_hash(
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            payload=payload,
            occurred_at=occurred_at,
            prev_hash=prev_hash,
            model_version=model_version,
            prompt_version=prompt_version,
        )
        return cls(
            id=entry_id,
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            payload=payload,
            occurred_at=occurred_at,
            prev_hash=prev_hash,
            entry_hash=entry_hash,
            model_version=model_version,
            prompt_version=prompt_version,
        )

    def recompute_hash(self) -> str:
        return compute_entry_hash(
            tenant_id=self.tenant_id,
            actor_id=self.actor_id,
            action=self.action,
            subject_type=self.subject_type,
            subject_id=self.subject_id,
            payload=self.payload,
            occurred_at=self.occurred_at,
            prev_hash=self.prev_hash,
            model_version=self.model_version,
            prompt_version=self.prompt_version,
        )


def compute_entry_hash(
    *,
    tenant_id: TenantId,
    actor_id: UserId | None,
    action: AuditAction,
    subject_type: str,
    subject_id: str | None,
    payload: dict[str, object],
    occurred_at: datetime,
    prev_hash: str,
    model_version: str | None = None,
    prompt_version: str | None = None,
) -> str:
    """SHA-256 over the previous hash concatenated with this entry's canonical form.

    ``id`` is deliberately excluded: it is assigned by the writer and carries no
    meaning, so including it would make hashes unreproducible from exported data.
    """
    body = _canonical(
        {
            "tenant_id": str(tenant_id),
            "actor_id": str(actor_id) if actor_id is not None else None,
            "action": action.value,
            "subject_type": subject_type,
            "subject_id": subject_id,
            "payload": payload,
            "occurred_at": occurred_at.isoformat(),
            "model_version": model_version,
            "prompt_version": prompt_version,
        }
    )
    return hashlib.sha256(f"{prev_hash}{body}".encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class ChainVerification:
    """Outcome of walking a tenant's chain."""

    tenant_id: TenantId
    entries_checked: int
    is_intact: bool
    broken_at: AuditEntryId | None = None
    reason: str | None = None


def verify_chain(tenant_id: TenantId, entries: list[AuditEntry]) -> ChainVerification:
    """Walk entries in chronological order and confirm the chain is unbroken.

    Detects three distinct tampers: a mutated payload (hash mismatch), a re-pointed
    link (prev_hash mismatch), and a deleted entry (which surfaces as the latter).
    """
    expected_prev = GENESIS_HASH
    for index, entry in enumerate(entries):
        if entry.prev_hash != expected_prev:
            return ChainVerification(
                tenant_id=tenant_id,
                entries_checked=index,
                is_intact=False,
                broken_at=entry.id,
                reason=(
                    "prev_hash does not match the preceding entry -- an entry was "
                    "deleted, reordered or re-pointed"
                ),
            )
        if entry.recompute_hash() != entry.entry_hash:
            return ChainVerification(
                tenant_id=tenant_id,
                entries_checked=index,
                is_intact=False,
                broken_at=entry.id,
                reason="entry_hash does not match the entry's contents -- it was mutated",
            )
        expected_prev = entry.entry_hash

    return ChainVerification(tenant_id=tenant_id, entries_checked=len(entries), is_intact=True)
