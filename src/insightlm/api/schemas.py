"""HTTP request and response models.

Pydantic parses at the edge; everything inside works with domain types. These models
never leak persistence detail -- note that no response carries ``password_hash`` or an
invitation ``token_hash``.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from insightlm.shared.domain.roles import Role


class RegisterOwnerRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tenant_name: str = Field(min_length=1, max_length=200)
    tenant_slug: str = Field(min_length=3, max_length=40, pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    tenant_slug: str | None = None


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"  # noqa: S105 - a scheme name, not a credential
    expires_in: int


class _FromDomain(BaseModel):
    """Built from a domain entity by attribute.

    Field-by-field, and only the fields declared here. That is what keeps
    ``password_hash`` and ``token_hash`` out of responses -- not vigilance, but the
    fact that they are not on the model.
    """

    model_config = ConfigDict(from_attributes=True)


class TenantResponse(_FromDomain):
    id: UUID
    name: str
    slug: str
    created_at: datetime


class UserResponse(_FromDomain):
    id: UUID
    email: str
    display_name: str
    is_active: bool


class MembershipResponse(_FromDomain):
    id: UUID
    tenant_id: UUID
    user_id: UUID
    role: Role
    created_at: datetime
    revoked_at: datetime | None = None


class RegistrationResponse(BaseModel):
    tenant: TenantResponse
    user: UserResponse
    membership: MembershipResponse


class MemberResponse(BaseModel):
    membership: MembershipResponse
    user: UserResponse


class InviteMemberRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: Role


class InvitationResponse(BaseModel):
    id: UUID
    email: str
    role: Role
    expires_at: datetime
    token: str = Field(
        description=(
            "Plaintext invitation token, returned exactly once. Only its hash is "
            "stored, so it cannot be recovered later."
        )
    )


class AcceptInvitationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=128)


class ChangeRoleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    role: Role


class AuditEntryResponse(_FromDomain):
    id: UUID
    actor_id: UUID | None
    action: str
    subject_type: str
    subject_id: str | None
    payload: dict[str, object]
    occurred_at: datetime
    prev_hash: str
    entry_hash: str


class ChainVerificationResponse(_FromDomain):
    tenant_id: UUID
    entries_checked: int
    is_intact: bool
    broken_at: UUID | None = None
    reason: str | None = None


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: dict[str, object] = Field(default_factory=dict)
    correlation_id: str | None = None
