"""The audit actions identity records."""

from __future__ import annotations

from insightlm.governance.domain.audit import AuditAction


class IdentityAudit:
    TENANT_CREATED = AuditAction("tenant.created")
    USER_REGISTERED = AuditAction("user.registered")
    USER_AUTHENTICATED = AuditAction("user.authenticated")
    INVITATION_CREATED = AuditAction("invitation.created")
    INVITATION_ACCEPTED = AuditAction("invitation.accepted")
    INVITATION_REVOKED = AuditAction("invitation.revoked")
    MEMBERSHIP_ROLE_CHANGED = AuditAction("membership.role_changed")
    MEMBERSHIP_REVOKED = AuditAction("membership.revoked")
