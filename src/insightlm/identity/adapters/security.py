"""Password hashing and access tokens."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher as Argon2Hasher
from argon2.exceptions import (
    InvalidHashError,
    VerificationError,
    VerifyMismatchError,
)

from insightlm.shared.domain.errors import AuthenticationError
from insightlm.shared.domain.ids import TenantId, UserId
from insightlm.shared.ports.tokens import TokenClaims


class Argon2PasswordHasher:
    """argon2id with the library's current defaults.

    ``needs_rehash`` lets us raise the cost parameters later and transparently upgrade
    each user's hash on their next successful login.
    """

    def __init__(self) -> None:
        self._hasher = Argon2Hasher()

    def hash(self, plaintext: str) -> str:
        return self._hasher.hash(plaintext)

    def verify(self, plaintext: str, hashed: str) -> bool:
        try:
            return self._hasher.verify(hashed, plaintext)
        except (VerifyMismatchError, VerificationError, InvalidHashError):
            # A malformed stored hash must read as "wrong password", never as an error
            # the caller has to handle -- otherwise it becomes an oracle.
            return False

    def needs_rehash(self, hashed: str) -> bool:
        try:
            return self._hasher.check_needs_rehash(hashed)
        except InvalidHashError:
            return True


class JwtTokenIssuer:
    """Short-lived HS256 access tokens.

    The token carries the tenant and role, so authorisation never needs a database
    round trip on the hot path. The cost is that a role change takes effect at the
    next token issue -- acceptable at a 15-minute TTL, and the trade-off is recorded
    in ADR-0003.
    """

    def __init__(self, secret: str, *, algorithm: str = "HS256", ttl_seconds: int = 900) -> None:
        self._secret = secret
        self._algorithm = algorithm
        self._ttl = timedelta(seconds=ttl_seconds)

    def issue_access_token(self, claims: TokenClaims) -> str:
        now = datetime.now(UTC)
        return jwt.encode(
            {
                "sub": str(claims.user_id),
                "tid": str(claims.tenant_id),
                "role": claims.role,
                "jti": claims.jti or uuid.uuid4().hex,
                "iat": int(now.timestamp()),
                "exp": int((now + self._ttl).timestamp()),
            },
            self._secret,
            algorithm=self._algorithm,
        )

    def verify_access_token(self, token: str) -> TokenClaims:
        try:
            payload = jwt.decode(
                token,
                self._secret,
                algorithms=[self._algorithm],
                options={"require": ["sub", "tid", "role", "exp"]},
            )
        except jwt.ExpiredSignatureError as exc:
            raise AuthenticationError("access token has expired") from exc
        except jwt.InvalidTokenError as exc:
            raise AuthenticationError("invalid access token") from exc

        try:
            return TokenClaims(
                user_id=UserId(uuid.UUID(payload["sub"])),
                tenant_id=TenantId(uuid.UUID(payload["tid"])),
                role=str(payload["role"]),
                jti=str(payload.get("jti", "")),
            )
        except (KeyError, ValueError) as exc:
            raise AuthenticationError("malformed access token claims") from exc
