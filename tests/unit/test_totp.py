"""The second factor's arithmetic, checked against the RFC's own test vectors."""

from __future__ import annotations

import base64
from datetime import UTC, datetime, timedelta

from insightlm.identity.domain import totp

# RFC 6238 appendix B uses the ASCII secret "12345678901234567890" for SHA-1.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode().rstrip("=")


def test_the_rfc_6238_sha1_vectors() -> None:
    vectors = {
        59: "94287082",
        1111111109: "07081804",
        1111111111: "14050471",
        1234567890: "89005924",
        2000000000: "69279037",
        20000000000: "65353130",
    }
    for seconds, expected in vectors.items():
        assert totp.code_for(RFC_SECRET, seconds // 30, digits=8) == expected


def test_a_current_code_verifies_and_reports_its_step() -> None:
    secret = totp.new_secret()
    now = datetime(2026, 11, 1, 12, 0, 15, tzinfo=UTC)
    code = totp.code_for(secret, totp.step_at(now))
    assert totp.verify(secret, code, now) == totp.step_at(now)


def test_thirty_seconds_of_drift_is_tolerated_and_more_is_not() -> None:
    secret = totp.new_secret()
    now = datetime(2026, 11, 1, 12, 0, 15, tzinfo=UTC)
    code = totp.code_for(secret, totp.step_at(now))
    assert totp.verify(secret, code, now + timedelta(seconds=30)) is not None
    assert totp.verify(secret, code, now + timedelta(seconds=90)) is None


def test_malformed_codes_are_refused_without_comparing() -> None:
    secret = totp.new_secret()
    now = datetime(2026, 11, 1, tzinfo=UTC)
    for bad in ("", "12345", "1234567", "12a456"):
        assert totp.verify(secret, bad, now) is None


def test_the_provisioning_uri_carries_what_an_app_needs() -> None:
    uri = totp.provisioning_uri("ABC", account="priya@acme.example")
    assert uri.startswith("otpauth://totp/InsightLM%3Apriya%40acme.example?")
    assert "secret=ABC" in uri and "issuer=InsightLM" in uri
