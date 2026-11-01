"""Time-based one-time passwords (RFC 6238), the codes authenticator apps show.

Standard library only, and small enough to read in one sitting -- which matters for a
second factor: the alternative is trusting a dependency with exactly the comparison an
attacker most wants to get wrong.

A code is HMAC-SHA1 over the number of 30-second steps since the epoch, truncated to six
digits (RFC 4226 section 5.3). One step either side is accepted, so a phone whose clock
has drifted by up to thirty seconds still works. The matched step is returned so the
caller can refuse to accept the same code twice.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from datetime import datetime
from urllib.parse import quote, urlencode

STEP_SECONDS = 30
DIGITS = 6
WINDOW = 1
"""Steps accepted either side of now: tolerates thirty seconds of clock drift."""


def new_secret() -> str:
    """160 random bits, base32 without padding, as authenticator apps expect."""
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def _key(secret: str) -> bytes:
    padded = secret.upper() + "=" * (-len(secret) % 8)
    return base64.b32decode(padded)


def step_at(moment: datetime) -> int:
    return int(moment.timestamp()) // STEP_SECONDS


def code_for(secret: str, step: int, *, digits: int = DIGITS) -> str:
    digest = hmac.new(_key(secret), struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10**digits).zfill(digits)


def verify(secret: str, code: str, moment: datetime) -> int | None:
    """The step the code belongs to, or None. Compared in constant time."""
    candidate = "".join(code.split())
    if len(candidate) != DIGITS or not candidate.isdigit():
        return None
    now = step_at(moment)
    matched = None
    for step in range(now - WINDOW, now + WINDOW + 1):
        if hmac.compare_digest(code_for(secret, step), candidate):
            matched = step
    return matched


def provisioning_uri(secret: str, *, account: str, issuer: str = "InsightLM") -> str:
    """The otpauth:// link an authenticator app reads from a QR code or a tap."""
    label = quote(f"{issuer}:{account}")
    query = urlencode(
        {"secret": secret, "issuer": issuer, "digits": DIGITS, "period": STEP_SECONDS}
    )
    return f"otpauth://totp/{label}?{query}"
