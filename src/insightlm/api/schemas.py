"""HTTP request and response models.

Pydantic parses at the edge; everything inside works with domain types. These models
never leak persistence detail -- note that no response carries ``password_hash`` or an
invitation ``token_hash``.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, BeforeValidator, ConfigDict, EmailStr, Field, model_validator

from insightlm.shared.domain.errors import ValidationError

_UNSTORABLE = re.compile(r"[\x00\ud800-\udfff]")


def reject_unstorable(value: str) -> str:
    """NUL and unpaired UTF-16 surrogates: valid in a Python string, but Postgres can't
    store NUL in text and neither encodes as UTF-8. Refused at the edge as a 422
    instead of surfacing as a 500 from the driver (found by schemathesis)."""
    if _UNSTORABLE.search(value):
        raise ValidationError(
            "text contains characters that can't be stored (NUL or a lone surrogate)"
        )
    return value


def _check(value: Any) -> Any:
    if isinstance(value, str):
        reject_unstorable(value)
    elif isinstance(value, dict):
        for item in value.values():
            _check(item)
    elif isinstance(value, list):
        for item in value:
            _check(item)
    return value


class RequestModel(BaseModel):
    """Base for every request body: unknown fields refused, unstorable text refused."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _storable(cls, data: Any) -> Any:
        return _check(data)


class RegisterOwnerRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    tenant_name: str = Field(min_length=1, max_length=200)
    tenant_slug: str = Field(min_length=3, max_length=40, pattern=r"^[a-z0-9][a-z0-9-]*[a-z0-9]$")
    email: EmailStr
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=128)


class LoginRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    password: str = Field(min_length=1, max_length=128)
    tenant_slug: str | None = None
    mfa_code: str | None = Field(default=None, min_length=6, max_length=8, pattern=r"^[0-9 ]+$")


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


def _value_of(value: object) -> object:
    """Roles and audit actions are value objects in the domain and names on the wire."""
    inner = getattr(value, "value", None)
    return inner if isinstance(inner, str) else value


RoleName = Annotated[str, BeforeValidator(_value_of)]
"""A role as the API sees it: its name. Which names exist is decided by the role
catalogue the running system was built with, so it is checked in the router, not by
an enum here."""

RoleInput = Annotated[str, Field(min_length=1, max_length=64, pattern=r"^[a-z][a-z_]*$")]


class MembershipResponse(_FromDomain):
    id: UUID
    tenant_id: UUID
    user_id: UUID
    role: RoleName
    created_at: datetime
    revoked_at: datetime | None = None


class RegistrationResponse(BaseModel):
    tenant: TenantResponse
    user: UserResponse
    membership: MembershipResponse


class MemberResponse(BaseModel):
    membership: MembershipResponse
    user: UserResponse


class InviteMemberRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    email: EmailStr
    role: RoleInput


class InvitationResponse(BaseModel):
    id: UUID
    email: str
    role: RoleName
    expires_at: datetime
    token: str = Field(
        description=(
            "Plaintext invitation token, returned exactly once. Only its hash is "
            "stored, so it cannot be recovered later."
        )
    )


class AcceptInvitationRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    token: str = Field(min_length=1, max_length=256)
    display_name: str = Field(min_length=1, max_length=200)
    password: str = Field(min_length=12, max_length=128)


class ChangeRoleRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    role: RoleInput


class AuditEntryResponse(_FromDomain):
    id: UUID
    actor_id: UUID | None
    action: Annotated[str, BeforeValidator(_value_of)]
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


# Postgres OFFSET is a bigint, so an unbounded offset from a client becomes a 500
# (found by schemathesis). Nobody pages past this by hand; bulk export is another API.
MAX_OFFSET = 100_000


class ErrorResponse(BaseModel):
    code: str
    message: str
    detail: dict[str, object] = Field(default_factory=dict)
    correlation_id: str | None = None


# ---------------------------------------------------------------- documents
class DocumentResponse(_FromDomain):
    id: UUID
    title: str
    media_type: str
    created_at: datetime


class DocumentVersionResponse(_FromDomain):
    id: UUID
    document_id: UUID
    version: int
    status: str
    byte_size: int
    page_count: int | None = None
    char_count: int | None = None
    failure_reason: str | None = None


class UploadResponse(BaseModel):
    document: DocumentResponse
    version: DocumentVersionResponse
    was_duplicate: bool = Field(
        description=(
            "True when identical bytes already existed in this workspace. The existing "
            "version is returned and no reprocessing is queued."
        )
    )


# ---------------------------------------------------------------- answering
class AskRequest(RequestModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=2000)
    top_k: int = Field(default=8, ge=1, le=50)
    document_ids: list[UUID] | None = Field(
        default=None, description="Restrict retrieval to these documents."
    )


class CitationResponse(BaseModel):
    label: str
    chunk_id: UUID
    document_id: UUID
    document_title: str
    page: int = Field(description="Zero-based page index in the source document.")
    char_start: int
    char_end: int
    quoted_text: str
    rects: list[dict[str, float]] = Field(
        default_factory=list,
        description=(
            "Highlight rectangles in PDF points, origin top-left. Empty for formats "
            "without fixed layout, where the viewer falls back to the quoted text."
        ),
    )


class ClaimResponse(BaseModel):
    text: str
    citations: list[str] = Field(description="Citation labels supporting this claim.")


class AnswerResponse(BaseModel):
    outcome: str = Field(description="answered | insufficient_evidence | validation_failed")
    text: str
    claims: list[ClaimResponse]
    citations: list[CitationResponse]
    refusal_reason: str | None = None

    strategy: str
    retrieval_config_hash: str
    model_version: str
    prompt_version: str
    embedding_model: str
    latency_ms: int
    input_tokens: int
    output_tokens: int
