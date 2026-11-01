"""Identity entities.

Invariants live in ``__post_init__``: an invalid ``Tenant`` or ``Invitation`` must be
unconstructable, so no use case has to re-check what the type already guarantees.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Self

from insightlm.shared.domain.errors import ConflictError, ValidationError
from insightlm.shared.domain.ids import (
    InvitationId,
    MembershipId,
    TenantId,
    UserId,
)
from insightlm.shared.domain.roles import PlatformRole, Role

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,38}[a-z0-9]$")


def normalise_email(raw: str) -> str:
    """Lower-cased and stripped. Used for storage *and* uniqueness."""
    email = raw.strip().lower()
    if not _EMAIL_RE.match(email):
        raise ValidationError("not a valid email address", detail={"email": raw})
    return email


@dataclass(frozen=True, slots=True)
class Tenant:
    id: TenantId
    name: str
    slug: str
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValidationError("tenant name must not be blank")
        if not _SLUG_RE.match(self.slug):
            raise ValidationError(
                "slug must be 3-40 chars, lowercase alphanumeric and hyphens",
                detail={"slug": self.slug},
            )


@dataclass(frozen=True, slots=True)
class User:
    """Global identity. Tenant association lives on :class:`Membership`."""

    id: UserId
    email: str
    display_name: str
    password_hash: str
    created_at: datetime
    is_active: bool = True
    mfa_sealed_secret: str | None = None
    """The authenticator secret, encrypted by a SecretBox. Opaque to the domain."""
    mfa_enabled: bool = False
    mfa_last_step: int = 0
    """The time step of the last code accepted. A code is never accepted twice."""

    def __post_init__(self) -> None:
        if self.email != self.email.strip().lower():
            raise ValidationError("email must be stored normalised")
        if not self.display_name.strip():
            raise ValidationError("display name must not be blank")

    def with_pending_mfa(self, sealed_secret: str) -> Self:
        """A new secret, not yet in force: sign-in is unchanged until it is confirmed."""
        if self.mfa_enabled:
            raise ConflictError("two-factor authentication is already on; turn it off first")
        return replace(self, mfa_sealed_secret=sealed_secret)

    def with_mfa_confirmed(self, step: int) -> Self:
        if self.mfa_sealed_secret is None:
            raise ConflictError("start two-factor enrolment before confirming it")
        return replace(self, mfa_enabled=True, mfa_last_step=step)

    def without_mfa(self) -> Self:
        return replace(self, mfa_enabled=False, mfa_sealed_secret=None, mfa_last_step=0)

    def accepting_step(self, step: int) -> Self:
        """Record a used code's step, refusing any step already used or older."""
        if step <= self.mfa_last_step:
            raise ConflictError("that code has already been used")
        return replace(self, mfa_last_step=step)


@dataclass(frozen=True, slots=True)
class Membership:
    """A user's role within one tenant."""

    id: MembershipId
    tenant_id: TenantId
    user_id: UserId
    role: Role
    created_at: datetime
    revoked_at: datetime | None = None

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None

    def with_role(self, role: Role) -> Self:
        if not self.is_active:
            raise ConflictError("cannot change the role of a revoked membership")
        return replace(self, role=role)

    def revoked(self, at: datetime) -> Self:
        if not self.is_active:
            raise ConflictError("membership is already revoked")
        return replace(self, revoked_at=at)


@dataclass(frozen=True, slots=True)
class Invitation:
    """A single-use, expiring invitation to join a tenant.

    Only the *hash* of the token secret is stored. The plaintext is returned once, at
    creation, and is unrecoverable afterwards -- the same discipline as a password.
    """

    id: InvitationId
    tenant_id: TenantId
    email: str
    role: Role
    token_hash: str
    invited_by: UserId
    created_at: datetime
    expires_at: datetime
    accepted_at: datetime | None = None
    accepted_by: UserId | None = None
    revoked_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.email != self.email.strip().lower():
            raise ValidationError("invitation email must be stored normalised")
        if self.expires_at <= self.created_at:
            raise ValidationError("invitation must expire after it is created")
        if self.role == PlatformRole.OWNER:
            raise ValidationError("ownership is transferred explicitly, never invited")

    def is_redeemable(self, *, now: datetime) -> bool:
        return self.accepted_at is None and self.revoked_at is None and now < self.expires_at

    def ensure_redeemable(self, *, now: datetime) -> None:
        """Raise with the precise reason this invitation cannot be redeemed.

        Separate from :meth:`accepted` so a caller can validate *before* creating the
        user account that acceptance would otherwise imply.
        """
        if self.accepted_at is not None:
            raise ConflictError("invitation has already been accepted")
        if self.revoked_at is not None:
            raise ConflictError("invitation has been revoked")
        if now >= self.expires_at:
            raise ConflictError("invitation has expired")

    def accepted(self, *, by: UserId, at: datetime) -> Self:
        """Redeem this invitation."""
        self.ensure_redeemable(now=at)
        return replace(self, accepted_at=at, accepted_by=by)

    def revoked(self, at: datetime) -> Self:
        if self.accepted_at is not None:
            raise ConflictError("cannot revoke an invitation that was already accepted")
        if self.revoked_at is not None:
            raise ConflictError("invitation is already revoked")
        return replace(self, revoked_at=at)
