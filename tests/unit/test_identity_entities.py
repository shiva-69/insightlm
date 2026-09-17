"""Entity invariants. An invalid entity must be unconstructable."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from insightlm.identity.domain.entities import (
    Invitation,
    Membership,
    Tenant,
    User,
    normalise_email,
)
from insightlm.shared.domain.errors import ConflictError, ValidationError
from insightlm.shared.domain.ids import (
    new_invitation_id,
    new_membership_id,
    new_tenant_id,
    new_user_id,
)
from insightlm.shared.domain.roles import Role

NOW = datetime(2026, 9, 18, 12, 0, tzinfo=UTC)


def make_invitation(**overrides: object) -> Invitation:
    defaults: dict[str, object] = {
        "id": new_invitation_id(),
        "tenant_id": new_tenant_id(),
        "email": "dev@example.com",
        "role": Role.RECRUITER,
        "token_hash": "a" * 64,
        "invited_by": new_user_id(),
        "created_at": NOW,
        "expires_at": NOW + timedelta(days=7),
    }
    return Invitation(**{**defaults, **overrides})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("  Dev@Example.COM ", "dev@example.com"), ("a.b+c@d.co", "a.b+c@d.co")],
)
def test_email_normalisation(raw: str, expected: str) -> None:
    assert normalise_email(raw) == expected


@pytest.mark.parametrize("raw", ["", "no-at-sign", "a@b", "a b@c.com", "@x.com"])
def test_invalid_emails_are_rejected(raw: str) -> None:
    with pytest.raises(ValidationError):
        normalise_email(raw)


@pytest.mark.parametrize("slug", ["ab", "-lead", "trail-", "Upper", "has space", "x" * 41])
def test_invalid_slugs_are_rejected(slug: str) -> None:
    with pytest.raises(ValidationError):
        Tenant(id=new_tenant_id(), name="Acme", slug=slug, created_at=NOW)


def test_invitation_cannot_grant_ownership() -> None:
    """Structural, not procedural: the entity itself refuses to exist."""
    with pytest.raises(ValidationError):
        make_invitation(role=Role.OWNER)


def test_invitation_must_expire_after_creation() -> None:
    with pytest.raises(ValidationError):
        make_invitation(expires_at=NOW - timedelta(seconds=1))


def test_invitation_is_single_use() -> None:
    accepted = make_invitation().accepted(by=new_user_id(), at=NOW)
    with pytest.raises(ConflictError, match="already been accepted"):
        accepted.accepted(by=new_user_id(), at=NOW)


def test_expired_invitation_cannot_be_accepted() -> None:
    invitation = make_invitation()
    with pytest.raises(ConflictError, match="expired"):
        invitation.accepted(by=new_user_id(), at=NOW + timedelta(days=8))


def test_revoked_invitation_cannot_be_accepted() -> None:
    revoked = make_invitation().revoked(NOW)
    with pytest.raises(ConflictError, match="revoked"):
        revoked.accepted(by=new_user_id(), at=NOW)


def test_accepted_invitation_cannot_be_revoked() -> None:
    accepted = make_invitation().accepted(by=new_user_id(), at=NOW)
    with pytest.raises(ConflictError):
        accepted.revoked(NOW)


def test_is_redeemable_tracks_every_terminal_state() -> None:
    invitation = make_invitation()
    assert invitation.is_redeemable(now=NOW)
    assert not invitation.is_redeemable(now=NOW + timedelta(days=8))
    assert not invitation.revoked(NOW).is_redeemable(now=NOW)
    assert not invitation.accepted(by=new_user_id(), at=NOW).is_redeemable(now=NOW)


def test_user_email_must_be_pre_normalised() -> None:
    """Storage is the wrong place to discover that a caller forgot to normalise."""
    with pytest.raises(ValidationError):
        User(
            id=new_user_id(),
            email="Mixed@Case.com",
            display_name="Dev",
            password_hash="x",
            created_at=NOW,
        )


def test_revoked_membership_cannot_change_role() -> None:
    membership = Membership(
        id=new_membership_id(),
        tenant_id=new_tenant_id(),
        user_id=new_user_id(),
        role=Role.RECRUITER,
        created_at=NOW,
    ).revoked(NOW)

    assert not membership.is_active
    with pytest.raises(ConflictError):
        membership.with_role(Role.INTERVIEWER)
