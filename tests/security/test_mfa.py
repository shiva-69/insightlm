"""Two-factor sign-in: enrolment that cannot lock anyone out, and codes used only once."""

from __future__ import annotations

import base64
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from insightlm.bootstrap import Container
from insightlm.identity.adapters.security import AesGcmSecretBox
from insightlm.identity.domain import totp
from insightlm.shared.domain.errors import AuthenticationError
from tests.conftest import requires_docker
from tests.integration.test_recruiting_flow import PASSWORD, auth

OWNER = "owner@mfa.example.com"


def code_at(secret: str, offset: int = 0) -> str:
    """A valid code for the current step, or one either side of it."""
    return totp.code_for(secret, totp.step_at(datetime.now(UTC)) + offset)


def test_the_secret_box_round_trips_and_refuses_tampering() -> None:
    box = AesGcmSecretBox("key material that is long enough")
    sealed = box.seal("JBSWY3DPEHPK3PXP")
    assert sealed.startswith("v1.") and "JBSWY3DP" not in sealed
    assert box.open(sealed) == "JBSWY3DPEHPK3PXP"
    with pytest.raises(AuthenticationError):
        AesGcmSecretBox("a different key entirely").open(sealed)
    # Flip one byte of the decoded ciphertext. (Editing a base64 character can land in
    # padding bits that decode to the same bytes, which would tamper with nothing.)
    raw = bytearray(base64.urlsafe_b64decode(sealed[3:]))
    raw[-1] ^= 0x01
    with pytest.raises(AuthenticationError):
        box.open("v1." + base64.urlsafe_b64encode(bytes(raw)).decode())


async def owner_token(client: AsyncClient, slug: str = "mfa") -> str:
    registered = await client.post(
        "/auth/register",
        json={
            "tenant_name": slug.title(),
            "tenant_slug": slug,
            "email": OWNER,
            "display_name": "Owner",
            "password": PASSWORD,
        },
    )
    assert registered.status_code == 201, registered.text
    login = await client.post("/auth/login", json={"email": OWNER, "password": PASSWORD})
    token: str = login.json()["access_token"]
    return token


@pytest.mark.integration
@requires_docker
async def test_enrolment_needs_a_working_code_then_sign_in_needs_one_every_time(
    client: AsyncClient, container: Container
) -> None:
    token = await owner_token(client)
    enrolled = (await client.post("/auth/mfa/enrol", headers=auth(token))).json()
    secret = enrolled["secret"]
    assert enrolled["provisioning_uri"].startswith("otpauth://totp/")

    # Started but not confirmed: sign-in is unchanged, so a botched setup locks no one out.
    plain = await client.post("/auth/login", json={"email": OWNER, "password": PASSWORD})
    assert plain.status_code == 200, plain.text

    wrong = await client.post("/auth/mfa/confirm", json={"code": "000000"}, headers=auth(token))
    assert wrong.status_code == 401, wrong.text
    confirmed = await client.post(
        "/auth/mfa/confirm", json={"code": code_at(secret, -1)}, headers=auth(token)
    )
    assert confirmed.status_code == 204, confirmed.text
    assert (await client.get("/auth/mfa", headers=auth(token))).json() == {"enabled": True}

    missing = await client.post("/auth/login", json={"email": OWNER, "password": PASSWORD})
    assert missing.status_code == 401, missing.text
    assert missing.json()["detail"]["mfa_required"] is True

    current = code_at(secret)
    signed_in = await client.post(
        "/auth/login", json={"email": OWNER, "password": PASSWORD, "mfa_code": current}
    )
    assert signed_in.status_code == 200, signed_in.text

    replayed = await client.post(
        "/auth/login", json={"email": OWNER, "password": PASSWORD, "mfa_code": current}
    )
    assert replayed.status_code == 401, replayed.text
    assert "already been used" in replayed.json()["message"]

    # A wrong password with a right code says nothing about the code.
    guessed = await client.post(
        "/auth/login",
        json={"email": OWNER, "password": "not-the-password", "mfa_code": code_at(secret, 1)},
    )
    assert guessed.status_code == 401 and "mfa_required" not in guessed.json().get("detail", {})


@pytest.mark.integration
@requires_docker
async def test_turning_it_off_needs_a_current_code_and_both_changes_are_audited(
    client: AsyncClient, container: Container
) -> None:
    token = await owner_token(client, "mfa-off")
    secret = (await client.post("/auth/mfa/enrol", headers=auth(token))).json()["secret"]
    await client.post("/auth/mfa/confirm", json={"code": code_at(secret, -1)}, headers=auth(token))

    refused = await client.post("/auth/mfa/disable", json={"code": "123456"}, headers=auth(token))
    assert refused.status_code == 401, refused.text
    disabled = await client.post(
        "/auth/mfa/disable", json={"code": code_at(secret)}, headers=auth(token)
    )
    assert disabled.status_code == 204, disabled.text
    assert (await client.get("/auth/mfa", headers=auth(token))).json() == {"enabled": False}

    actions = [
        e["action"] for e in (await client.get("/audit/entries", headers=auth(token))).json()
    ]
    assert "user.mfa_enabled" in actions and "user.mfa_disabled" in actions
