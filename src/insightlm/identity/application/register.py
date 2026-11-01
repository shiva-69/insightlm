"""Tenant registration: the only way an Owner comes into existence."""

from __future__ import annotations

from insightlm.identity.application.commands import RegisterOwnerCommand, RegisterOwnerResult
from insightlm.identity.domain.audit_actions import IdentityAudit
from insightlm.identity.domain.entities import Membership, Tenant, User, normalise_email
from insightlm.identity.ports.uow import IdentityUnitOfWorkFactory
from insightlm.shared.domain.errors import ConflictError
from insightlm.shared.domain.ids import (
    new_membership_id,
    new_outbox_event_id,
    new_tenant_id,
    new_user_id,
)
from insightlm.shared.domain.roles import PlatformRole
from insightlm.shared.ports.clock import Clock
from insightlm.shared.ports.outbox import OutboxEvent
from insightlm.shared.ports.password import PasswordHasher


class RegisterOwner:
    def __init__(
        self,
        uow_factory: IdentityUnitOfWorkFactory,
        hasher: PasswordHasher,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._hasher = hasher
        self._clock = clock

    async def execute(self, command: RegisterOwnerCommand) -> RegisterOwnerResult:
        email = normalise_email(command.email)
        now = self._clock.now()

        # The tenant id is generated up front so the unit of work can establish the
        # row-level security context *before* inserting the tenant row -- the policy's
        # WITH CHECK clause compares the new row's id against it.
        tenant_id = new_tenant_id()

        async with self._uow_factory(tenant_id=tenant_id) as uow:
            if await uow.users.get_by_email(email) is not None:
                raise ConflictError(
                    "an account with this email already exists", detail={"email": email}
                )

            tenant = Tenant(
                id=tenant_id,
                name=command.tenant_name.strip(),
                slug=command.tenant_slug.strip().lower(),
                created_at=now,
            )
            user = User(
                id=new_user_id(),
                email=email,
                display_name=command.display_name.strip(),
                password_hash=self._hasher.hash(command.password),
                created_at=now,
            )
            membership = Membership(
                id=new_membership_id(),
                tenant_id=tenant.id,
                user_id=user.id,
                role=PlatformRole.OWNER,
                created_at=now,
            )

            await uow.tenants.add(tenant)
            await uow.users.add(user)
            await uow.memberships.add(membership)

            await uow.audit.record(
                tenant_id=tenant.id,
                actor_id=user.id,
                action=IdentityAudit.TENANT_CREATED,
                subject_type="tenant",
                subject_id=str(tenant.id),
                payload={"slug": tenant.slug, "name": tenant.name},
            )
            await uow.audit.record(
                tenant_id=tenant.id,
                actor_id=user.id,
                action=IdentityAudit.USER_REGISTERED,
                subject_type="user",
                subject_id=str(user.id),
                payload={"role": PlatformRole.OWNER.value},
            )

            uow.outbox.add(
                OutboxEvent(
                    id=new_outbox_event_id(),
                    tenant_id=tenant.id,
                    event_type="tenant.registered",
                    payload={"tenant_id": str(tenant.id), "owner_user_id": str(user.id)},
                    occurred_at=now,
                )
            )

            await uow.commit()

        return RegisterOwnerResult(tenant=tenant, user=user, membership=membership)
