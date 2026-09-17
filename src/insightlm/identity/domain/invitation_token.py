"""Invitation token format.

``<base64url(tenant_uuid)>.<base64url(32 random bytes)>``

The tenant is carried *in* the token on purpose. Accepting an invitation is the one
flow where the caller has no session yet, so there is no tenant context to satisfy
the row-level security policies. Reading the tenant from the token lets the use case
establish that context before it queries -- which means the policies stay
fail-closed instead of needing an "unauthenticated" exemption (see ADR-0016).

Only ``sha256(secret)`` is persisted, so a database leak does not yield usable tokens.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from uuid import UUID

from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.domain.ids import TenantId

_SECRET_BYTES = 32


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _b64d(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


@dataclass(frozen=True, slots=True)
class InvitationToken:
    tenant_id: TenantId
    secret: str

    def __str__(self) -> str:
        return f"{_b64e(self.tenant_id.bytes)}.{self.secret}"

    @property
    def hashed(self) -> str:
        return hash_secret(self.secret)


def issue(tenant_id: TenantId) -> InvitationToken:
    return InvitationToken(tenant_id=tenant_id, secret=_b64e(secrets.token_bytes(_SECRET_BYTES)))


def parse(raw: str) -> InvitationToken:
    """Parse a token, raising :class:`ValidationError` on anything malformed."""
    tenant_part, _, secret = raw.strip().partition(".")
    if not tenant_part or not secret:
        raise ValidationError("malformed invitation token")
    try:
        tenant_id = TenantId(UUID(bytes=_b64d(tenant_part)))
    except (ValueError, TypeError) as exc:
        raise ValidationError("malformed invitation token") from exc
    return InvitationToken(tenant_id=tenant_id, secret=secret)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def matches(secret: str, token_hash: str) -> bool:
    """Constant-time comparison -- token lookup must not be a timing oracle."""
    return secrets.compare_digest(hash_secret(secret), token_hash)
