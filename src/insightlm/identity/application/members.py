"""Listing and managing workspace members."""

from __future__ import annotations

from dataclasses import dataclass

from insightlm.identity.domain.audit_actions import IdentityAudit
from insightlm.identity.domain.entities import Membership, User
from insightlm.identity.ports.uow import IdentityUnitOfWorkFactory
from insightlm.shared.domain.errors import ConflictError, NotFoundError, ValidationError
from insightlm.shared.domain.ids import MembershipId
from insightlm.shared.domain.policy import Actor, require
from insightlm.shared.domain.roles import PlatformPermission, PlatformRole, Role, RoleCatalogue
from insightlm.shared.ports.clock import Clock


@dataclass(frozen=True, slots=True)
class MemberView:
    membership: Membership
    user: User


class ListMembers:
    def __init__(self, uow_factory: IdentityUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: Actor) -> list[MemberView]:
        async with self._uow_factory(tenant_id=actor.tenant_id) as uow:
            memberships = await uow.memberships.list_for_tenant(actor.tenant_id)
            views: list[MemberView] = []
            for membership in memberships:
                user = await uow.users.get(membership.user_id)
                if user is not None:
                    views.append(MemberView(membership=membership, user=user))
            return views


class ChangeMemberRole:
    def __init__(
        self, uow_factory: IdentityUnitOfWorkFactory, clock: Clock, roles: RoleCatalogue
    ) -> None:
        self._uow_factory = uow_factory
        self._clock = clock
        self._roles = roles

    async def execute(
        self, actor: Actor, membership_id: MembershipId, new_role: Role
    ) -> Membership:
        require(actor, PlatformPermission.MEMBER_MANAGE)

        if new_role not in self._roles.assignable:
            raise ValidationError(
                "ownership is transferred through a dedicated flow, not a role change",
                detail={"role": new_role.value},
            )

        async with self._uow_factory(tenant_id=actor.tenant_id) as uow:
            membership = await uow.memberships.get(membership_id)
            # RLS already scopes the read to this tenant; the explicit check documents
            # the intent and survives anyone later querying without a tenant context.
            if membership is None or membership.tenant_id != actor.tenant_id:
                raise NotFoundError("membership not found")
            if membership.role == PlatformRole.OWNER:
                raise ConflictError("the owner's role cannot be changed here")

            previous = membership.role
            updated = membership.with_role(new_role)
            await uow.memberships.update(updated)

            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=IdentityAudit.MEMBERSHIP_ROLE_CHANGED,
                subject_type="membership",
                subject_id=str(membership_id),
                payload={"from": previous.value, "to": new_role.value},
            )
            await uow.commit()
            return updated


class RevokeMembership:
    def __init__(self, uow_factory: IdentityUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, actor: Actor, membership_id: MembershipId) -> Membership:
        require(actor, PlatformPermission.MEMBER_MANAGE)

        async with self._uow_factory(tenant_id=actor.tenant_id) as uow:
            membership = await uow.memberships.get(membership_id)
            if membership is None or membership.tenant_id != actor.tenant_id:
                raise NotFoundError("membership not found")
            if membership.role == PlatformRole.OWNER:
                raise ConflictError("the workspace owner cannot be removed")
            if membership.user_id == actor.user_id:
                raise ConflictError("you cannot remove your own membership")

            revoked = membership.revoked(self._clock.now())
            await uow.memberships.update(revoked)

            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=IdentityAudit.MEMBERSHIP_REVOKED,
                subject_type="membership",
                subject_id=str(membership_id),
                payload={"user_id": str(membership.user_id), "role": membership.role.value},
            )
            await uow.commit()
            return revoked
