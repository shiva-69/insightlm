"""Typed identifiers.

`NewType` over `UUID` gives compile-time safety with zero runtime cost: passing a
``UserId`` where a ``TenantId`` is expected is a mypy error, but the value is still
just a UUID at runtime.
"""

from __future__ import annotations

from typing import NewType
from uuid import UUID, uuid4

TenantId = NewType("TenantId", UUID)
UserId = NewType("UserId", UUID)
MembershipId = NewType("MembershipId", UUID)
InvitationId = NewType("InvitationId", UUID)
AuditEntryId = NewType("AuditEntryId", UUID)
OutboxEventId = NewType("OutboxEventId", UUID)
CorrelationId = NewType("CorrelationId", str)


def new_tenant_id() -> TenantId:
    return TenantId(uuid4())


def new_user_id() -> UserId:
    return UserId(uuid4())


def new_membership_id() -> MembershipId:
    return MembershipId(uuid4())


def new_invitation_id() -> InvitationId:
    return InvitationId(uuid4())


def new_audit_entry_id() -> AuditEntryId:
    return AuditEntryId(uuid4())


def new_outbox_event_id() -> OutboxEventId:
    return OutboxEventId(uuid4())
