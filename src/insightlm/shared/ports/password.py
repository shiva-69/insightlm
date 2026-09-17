"""Password hashing, behind a port so the algorithm is swappable and testable."""

from __future__ import annotations

from typing import Protocol


class PasswordHasher(Protocol):
    def hash(self, plaintext: str) -> str: ...

    def verify(self, plaintext: str, hashed: str) -> bool:
        """Constant-time verification. Returns False on malformed hashes, never raises."""
        ...

    def needs_rehash(self, hashed: str) -> bool:
        """True when the hash was produced with outdated parameters."""
        ...
