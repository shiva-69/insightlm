"""Access-token issuing and verification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from insightlm.shared.domain.ids import TenantId, UserId


@dataclass(frozen=True, slots=True)
class TokenClaims:
    user_id: UserId
    tenant_id: TenantId
    role: str
    jti: str


class TokenIssuer(Protocol):
    def issue_access_token(self, claims: TokenClaims) -> str: ...

    def verify_access_token(self, token: str) -> TokenClaims:
        """Raises :class:`AuthenticationError` when invalid, expired or malformed."""
        ...
