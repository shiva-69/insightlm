"""Invitation tokens carry the tenant so an unauthenticated flow can satisfy RLS."""

from __future__ import annotations

import pytest

from insightlm.identity.domain import invitation_token
from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.domain.ids import new_tenant_id


def test_round_trips_the_tenant() -> None:
    tenant = new_tenant_id()
    token = invitation_token.issue(tenant)
    assert invitation_token.parse(str(token)).tenant_id == tenant


def test_secret_is_never_recoverable_from_the_stored_hash() -> None:
    token = invitation_token.issue(new_tenant_id())
    assert token.secret not in token.hashed
    assert len(token.hashed) == 64


def test_tokens_are_unique() -> None:
    tenant = new_tenant_id()
    secrets = {invitation_token.issue(tenant).secret for _ in range(200)}
    assert len(secrets) == 200


def test_matches_accepts_the_right_secret_and_rejects_others() -> None:
    token = invitation_token.issue(new_tenant_id())
    assert invitation_token.matches(token.secret, token.hashed)
    assert not invitation_token.matches("wrong", token.hashed)


@pytest.mark.parametrize(
    "raw",
    ["", ".", "nodot", "....", "!!!.secret", "onlytenant.", ".onlysecret"],
)
def test_malformed_tokens_are_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        invitation_token.parse(raw)


def test_a_token_for_one_tenant_does_not_parse_as_another() -> None:
    a, b = new_tenant_id(), new_tenant_id()
    assert invitation_token.parse(str(invitation_token.issue(a))).tenant_id != b
