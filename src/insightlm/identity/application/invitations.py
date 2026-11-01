"""Inviting teammates, and redeeming those invitations.

This is the flow behind "a recruiter should be able to add members who help with
recruitment" -- with the constraint that an invitation is single-use, expiring, and
never grants ownership.
"""

from __future__ import annotations

from datetime import timedelta

from insightlm.identity.application.commands import (
    AcceptInvitationCommand,
    AcceptInvitationResult,
    InviteMemberCommand,
    InviteMemberResult,
)
from insightlm.identity.domain import invitation_token
from insightlm.identity.domain.audit_actions import IdentityAudit
from insightlm.identity.domain.entities import (
    Invitation,
    Membership,
    User,
    normalise_email,
)
from insightlm.identity.ports.uow import IdentityUnitOfWorkFactory
from insightlm.shared.domain.errors import (
    ConflictError,
    NotFoundError,
    ValidationError,
)
from insightlm.shared.domain.ids import (
    new_invitation_id,
    new_membership_id,
    new_outbox_event_id,
    new_user_id,
)
from insightlm.shared.domain.policy import Actor, require
from insightlm.shared.domain.roles import PlatformPermission, RoleCatalogue
from insightlm.shared.ports.clock import Clock
from insightlm.shared.ports.outbox import OutboxEvent
from insightlm.shared.ports.password import PasswordHasher


class InviteMember:
    def __init__(
        self,
        uow_factory: IdentityUnitOfWorkFactory,
        clock: Clock,
        roles: RoleCatalogue,
        *,
        invitation_ttl_seconds: int,
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._roles = roles
        self._ttl = timedelta(seconds=invitation_ttl_seconds)

    async def execute(self, actor: Actor, command: InviteMemberCommand) -> InviteMemberResult:
        require(actor, PlatformPermission.MEMBER_INVITE)

        if command.role not in self._roles.assignable:
            raise ValidationError(
                "this role cannot be granted by invitation",
                detail={"role": command.role.value},
            )

        email = normalise_email(command.email)
        now = self._clock.now()
        token = invitation_token.issue(actor.tenant_id)

        async with self._uow_factory(tenant_id=actor.tenant_id) as uow:
            existing_user = await uow.users.get_by_email(email)
            if existing_user is not None:
                membership = await uow.memberships.get_for_user(actor.tenant_id, existing_user.id)
                if membership is not None and membership.is_active:
                    raise ConflictError(
                        "this person is already a member of the workspace",
                        detail={"email": email},
                    )

            invitation = Invitation(
                id=new_invitation_id(),
                tenant_id=actor.tenant_id,
                email=email,
                role=command.role,
                token_hash=token.hashed,
                invited_by=actor.user_id,
                created_at=now,
                expires_at=now + self._ttl,
            )
            await uow.invitations.add(invitation)

            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=IdentityAudit.INVITATION_CREATED,
                subject_type="invitation",
                subject_id=str(invitation.id),
                # The email is the subject of the invitation, so recording it is the
                # point. The token is never logged or audited.
                payload={"email": email, "role": command.role.value},
            )
            uow.outbox.add(
                OutboxEvent(
                    id=new_outbox_event_id(),
                    tenant_id=actor.tenant_id,
                    event_type="invitation.created",
                    payload={"invitation_id": str(invitation.id), "email": email},
                    occurred_at=now,
                )
            )
            await uow.commit()

        return InviteMemberResult(invitation=invitation, token=str(token))


class AcceptInvitation:
    """Redeems a token, creating the user account if this is their first workspace."""

    def __init__(
        self,
        uow_factory: IdentityUnitOfWorkFactory,
        hasher: PasswordHasher,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._hasher = hasher
        self._clock = clock

    async def execute(self, command: AcceptInvitationCommand) -> AcceptInvitationResult:
        parsed = invitation_token.parse(command.token)
        now = self._clock.now()

        # The tenant comes from the token, which is what lets this unauthenticated
        # flow satisfy the RLS policies without an exemption. See ADR-0016.
        async with self._uow_factory(tenant_id=parsed.tenant_id) as uow:
            invitation = await uow.invitations.get_by_token_hash(
                parsed.tenant_id, invitation_token.hash_secret(parsed.secret)
            )
            if invitation is None:
                raise NotFoundError("invitation not found or already used")

            # Validate before creating anything -- the rollback would undo a user
            # insert, but failing loudly up front is clearer than relying on it.
            invitation.ensure_redeemable(now=now)

            tenant = await uow.tenants.get(parsed.tenant_id)
            if tenant is None:
                raise NotFoundError("workspace no longer exists")

            user = await uow.users.get_by_email(invitation.email)
            if user is None:
                user = User(
                    id=new_user_id(),
                    email=invitation.email,
                    display_name=command.display_name.strip(),
                    password_hash=self._hasher.hash(command.password),
                    created_at=now,
                )
                await uow.users.add(user)

            invitation = invitation.accepted(by=user.id, at=now)

            existing = await uow.memberships.get_for_user(tenant.id, user.id)
            if existing is not None and existing.is_active:
                raise ConflictError("this person is already a member of the workspace")

            membership = Membership(
                id=new_membership_id(),
                tenant_id=tenant.id,
                user_id=user.id,
                role=invitation.role,
                created_at=now,
            )
            await uow.memberships.add(membership)
            await uow.invitations.update(invitation)

            await uow.audit.record(
                tenant_id=tenant.id,
                actor_id=user.id,
                action=IdentityAudit.INVITATION_ACCEPTED,
                subject_type="invitation",
                subject_id=str(invitation.id),
                payload={"role": membership.role.value, "user_id": str(user.id)},
            )
            await uow.commit()

        return AcceptInvitationResult(tenant=tenant, user=user, membership=membership)
