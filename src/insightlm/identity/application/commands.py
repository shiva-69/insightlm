"""Command and result types for identity use cases."""

from __future__ import annotations

from dataclasses import dataclass

from insightlm.identity.domain.entities import Invitation, Membership, Tenant, User
from insightlm.shared.domain.roles import Role


@dataclass(frozen=True, slots=True)
class RegisterOwnerCommand:
    tenant_name: str
    tenant_slug: str
    email: str
    display_name: str
    password: str


@dataclass(frozen=True, slots=True)
class RegisterOwnerResult:
    tenant: Tenant
    user: User
    membership: Membership


@dataclass(frozen=True, slots=True)
class AuthenticateCommand:
    email: str
    password: str
    tenant_slug: str | None = None
    """Omitted when the user belongs to exactly one tenant."""


@dataclass(frozen=True, slots=True)
class AuthenticateResult:
    access_token: str
    expires_in: int
    user: User
    membership: Membership


@dataclass(frozen=True, slots=True)
class InviteMemberCommand:
    email: str
    role: Role


@dataclass(frozen=True, slots=True)
class InviteMemberResult:
    invitation: Invitation
    token: str
    """Plaintext, returned exactly once. Only its hash is persisted."""


@dataclass(frozen=True, slots=True)
class AcceptInvitationCommand:
    token: str
    display_name: str
    password: str
    """Ignored when the invited email already has an account."""


@dataclass(frozen=True, slots=True)
class AcceptInvitationResult:
    tenant: Tenant
    user: User
    membership: Membership
