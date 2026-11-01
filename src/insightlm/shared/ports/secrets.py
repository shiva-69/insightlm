"""Encrypting small secrets at rest -- authenticator seeds, for now."""

from __future__ import annotations

from typing import Protocol


class SecretBox(Protocol):
    def seal(self, plaintext: str) -> str:
        """Encrypt and authenticate. The result is safe to store; it is opaque text."""
        ...

    def open(self, sealed: str) -> str:
        """Decrypt, refusing anything tampered with or sealed under another key."""
        ...
