"""The Phase 0 exit criterion, end to end.

Register a workspace, log in, invite a teammate, have them accept, and confirm the
audit chain recorded all of it and still verifies.
"""

from __future__ import annotations

import pytest

from insightlm.bootstrap import Container, build_role_catalogue
from insightlm.identity.application.commands import (
    AcceptInvitationCommand,
    AuthenticateCommand,
    InviteMemberCommand,
    RegisterOwnerCommand,
)
from insightlm.recruiting.domain.roles import RecruitingRole
from insightlm.shared.domain.errors import (
    AuthenticationError,
    ConflictError,
    NotFoundError,
    PermissionDeniedError,
    ValidationError,
)
from insightlm.shared.domain.policy import Actor
from insightlm.shared.domain.roles import PlatformRole
from tests.conftest import requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

PASSWORD = "correct-horse-battery-staple"


async def register_owner(container: Container, slug: str = "acme") -> Actor:
    result = await container.identity.register_owner.execute(
        RegisterOwnerCommand(
            tenant_name=slug.title(),
            tenant_slug=slug,
            email=f"owner@{slug}.example.com",
            display_name="Owner",
            password=PASSWORD,
        )
    )
    return Actor.of(
        build_role_catalogue(),
        user_id=result.user.id,
        tenant_id=result.tenant.id,
        role=PlatformRole.OWNER,
    )


async def test_register_login_invite_accept(container: Container) -> None:
    owner = await register_owner(container)

    login = await container.identity.authenticate.execute(
        AuthenticateCommand(email="owner@acme.example.com", password=PASSWORD)
    )
    assert login.access_token
    assert login.membership.role == PlatformRole.OWNER

    claims = container.token_issuer.verify_access_token(login.access_token)
    assert claims.tenant_id == owner.tenant_id
    assert claims.role == PlatformRole.OWNER.value

    invite = await container.identity.invite_member.execute(
        owner,
        InviteMemberCommand(email="recruiter@acme.example.com", role=RecruitingRole.RECRUITER),
    )
    assert invite.token
    assert invite.invitation.token_hash not in invite.token

    accepted = await container.identity.accept_invitation.execute(
        AcceptInvitationCommand(token=invite.token, display_name="New Recruiter", password=PASSWORD)
    )
    assert accepted.membership.role == RecruitingRole.RECRUITER
    assert accepted.tenant.id == owner.tenant_id

    members = await container.identity.list_members.execute(owner)
    assert {m.user.email for m in members} == {
        "owner@acme.example.com",
        "recruiter@acme.example.com",
    }

    verification = await container.governance.verify_audit_chain.execute(owner)
    assert verification.is_intact
    # tenant.created, user.registered, user.authenticated, invitation.created,
    # invitation.accepted
    assert verification.entries_checked == 5


async def test_an_invitation_is_single_use(container: Container) -> None:
    owner = await register_owner(container)
    invite = await container.identity.invite_member.execute(
        owner, InviteMemberCommand(email="dev@acme.example.com", role=RecruitingRole.INTERVIEWER)
    )
    command = AcceptInvitationCommand(token=invite.token, display_name="Dev", password=PASSWORD)

    await container.identity.accept_invitation.execute(command)
    with pytest.raises(ConflictError, match="already been accepted"):
        await container.identity.accept_invitation.execute(command)


async def test_a_forged_token_is_rejected(container: Container) -> None:
    owner = await register_owner(container)
    invite = await container.identity.invite_member.execute(
        owner, InviteMemberCommand(email="dev@acme.example.com", role=RecruitingRole.INTERVIEWER)
    )
    tenant_part, _, _ = invite.token.partition(".")

    with pytest.raises(NotFoundError):
        await container.identity.accept_invitation.execute(
            AcceptInvitationCommand(
                token=f"{tenant_part}.forged-secret", display_name="X", password=PASSWORD
            )
        )


async def test_ownership_cannot_be_invited(container: Container) -> None:
    owner = await register_owner(container)
    with pytest.raises(ValidationError, match="cannot be granted by invitation"):
        await container.identity.invite_member.execute(
            owner, InviteMemberCommand(email="x@acme.example.com", role=PlatformRole.OWNER)
        )


async def test_an_interviewer_cannot_invite(container: Container) -> None:
    owner = await register_owner(container)
    invite = await container.identity.invite_member.execute(
        owner, InviteMemberCommand(email="ivr@acme.example.com", role=RecruitingRole.INTERVIEWER)
    )
    accepted = await container.identity.accept_invitation.execute(
        AcceptInvitationCommand(token=invite.token, display_name="Ivr", password=PASSWORD)
    )
    interviewer = Actor.of(
        build_role_catalogue(),
        user_id=accepted.user.id,
        tenant_id=accepted.tenant.id,
        role=RecruitingRole.INTERVIEWER,
    )

    with pytest.raises(PermissionDeniedError):
        await container.identity.invite_member.execute(
            interviewer,
            InviteMemberCommand(email="y@acme.example.com", role=RecruitingRole.RECRUITER),
        )


async def test_an_interviewer_cannot_read_the_audit_log(container: Container) -> None:
    owner = await register_owner(container)
    interviewer = Actor.of(
        build_role_catalogue(),
        user_id=owner.user_id,
        tenant_id=owner.tenant_id,
        role=RecruitingRole.INTERVIEWER,
    )
    with pytest.raises(PermissionDeniedError):
        await container.governance.list_audit_entries.execute(interviewer)


async def test_duplicate_slug_is_rejected_by_the_constraint(container: Container) -> None:
    """Under RLS a uniqueness SELECT cannot see the conflicting row, so the constraint
    is the only honest arbiter -- and the unit of work translates it to a domain error.
    """
    await register_owner(container, "acme")
    with pytest.raises(ConflictError):
        await container.identity.register_owner.execute(
            RegisterOwnerCommand(
                tenant_name="Acme Two",
                tenant_slug="acme",
                email="other@example.com",
                display_name="Other",
                password=PASSWORD,
            )
        )


async def test_wrong_password_and_unknown_email_fail_identically(container: Container) -> None:
    """No user-enumeration oracle: both paths return the same message."""
    await register_owner(container)

    with pytest.raises(AuthenticationError) as wrong:
        await container.identity.authenticate.execute(
            AuthenticateCommand(email="owner@acme.example.com", password="wrong-password-here")
        )
    with pytest.raises(AuthenticationError) as unknown:
        await container.identity.authenticate.execute(
            AuthenticateCommand(email="nobody@acme.example.com", password="wrong-password-here")
        )

    assert str(wrong.value) == str(unknown.value)
