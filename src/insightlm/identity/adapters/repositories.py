"""SQLAlchemy repositories for identity.

Each repository owns the translation between its ORM row and its domain entity. The
mapping is explicit and boring on purpose -- it is the seam that keeps SQLAlchemy out
of the domain layer.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from insightlm.identity.adapters.models import (
    InvitationRow,
    MembershipRow,
    TenantRow,
    UserRow,
)
from insightlm.identity.domain.entities import Invitation, Membership, Tenant, User
from insightlm.shared.domain.errors import NotFoundError
from insightlm.shared.domain.ids import (
    InvitationId,
    MembershipId,
    TenantId,
    UserId,
)
from insightlm.shared.domain.roles import Role


def _to_tenant(row: TenantRow) -> Tenant:
    return Tenant(id=TenantId(row.id), name=row.name, slug=row.slug, created_at=row.created_at)


def _to_user(row: UserRow) -> User:
    return User(
        id=UserId(row.id),
        email=row.email,
        display_name=row.display_name,
        password_hash=row.password_hash,
        is_active=row.is_active,
        created_at=row.created_at,
    )


def _to_membership(row: MembershipRow) -> Membership:
    return Membership(
        id=MembershipId(row.id),
        tenant_id=TenantId(row.tenant_id),
        user_id=UserId(row.user_id),
        role=Role(row.role),
        created_at=row.created_at,
        revoked_at=row.revoked_at,
    )


def _to_invitation(row: InvitationRow) -> Invitation:
    return Invitation(
        id=InvitationId(row.id),
        tenant_id=TenantId(row.tenant_id),
        email=row.email,
        role=Role(row.role),
        token_hash=row.token_hash,
        invited_by=UserId(row.invited_by),
        created_at=row.created_at,
        expires_at=row.expires_at,
        accepted_at=row.accepted_at,
        accepted_by=UserId(row.accepted_by) if row.accepted_by else None,
        revoked_at=row.revoked_at,
    )


class SqlAlchemyTenantRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, tenant: Tenant) -> None:
        self._session.add(
            TenantRow(
                id=tenant.id, name=tenant.name, slug=tenant.slug, created_at=tenant.created_at
            )
        )

    async def get(self, tenant_id: TenantId) -> Tenant | None:
        row = await self._session.get(TenantRow, tenant_id)
        return _to_tenant(row) if row else None

    async def get_by_slug(self, slug: str) -> Tenant | None:
        row = (
            await self._session.execute(select(TenantRow).where(TenantRow.slug == slug))
        ).scalar_one_or_none()
        return _to_tenant(row) if row else None


class SqlAlchemyUserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, user: User) -> None:
        self._session.add(
            UserRow(
                id=user.id,
                email=user.email,
                display_name=user.display_name,
                password_hash=user.password_hash,
                is_active=user.is_active,
                created_at=user.created_at,
            )
        )

    async def get(self, user_id: UserId) -> User | None:
        row = await self._session.get(UserRow, user_id)
        return _to_user(row) if row else None

    async def get_by_email(self, email: str) -> User | None:
        row = (
            await self._session.execute(select(UserRow).where(UserRow.email == email))
        ).scalar_one_or_none()
        return _to_user(row) if row else None

    async def update(self, user: User) -> None:
        row = await self._session.get(UserRow, user.id)
        if row is None:
            raise NotFoundError("user not found", detail={"user_id": str(user.id)})
        row.display_name = user.display_name
        row.password_hash = user.password_hash
        row.is_active = user.is_active


class SqlAlchemyMembershipRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, membership: Membership) -> None:
        self._session.add(
            MembershipRow(
                id=membership.id,
                tenant_id=membership.tenant_id,
                user_id=membership.user_id,
                role=membership.role.value,
                created_at=membership.created_at,
                revoked_at=membership.revoked_at,
            )
        )

    async def get(self, membership_id: MembershipId) -> Membership | None:
        row = await self._session.get(MembershipRow, membership_id)
        return _to_membership(row) if row else None

    async def get_for_user(self, tenant_id: TenantId, user_id: UserId) -> Membership | None:
        row = (
            await self._session.execute(
                select(MembershipRow).where(
                    MembershipRow.tenant_id == tenant_id,
                    MembershipRow.user_id == user_id,
                )
            )
        ).scalar_one_or_none()
        return _to_membership(row) if row else None

    async def list_for_tenant(self, tenant_id: TenantId) -> list[Membership]:
        rows = (
            await self._session.execute(
                select(MembershipRow)
                .where(MembershipRow.tenant_id == tenant_id)
                .order_by(MembershipRow.created_at)
            )
        ).scalars()
        return [_to_membership(row) for row in rows]

    async def list_for_user(self, user_id: UserId) -> list[Membership]:
        rows = (
            await self._session.execute(
                select(MembershipRow)
                .where(MembershipRow.user_id == user_id, MembershipRow.revoked_at.is_(None))
                .order_by(MembershipRow.created_at)
            )
        ).scalars()
        return [_to_membership(row) for row in rows]

    async def update(self, membership: Membership) -> None:
        row = await self._session.get(MembershipRow, membership.id)
        if row is None:
            raise NotFoundError("membership not found", detail={"id": str(membership.id)})
        row.role = membership.role.value
        row.revoked_at = membership.revoked_at


class SqlAlchemyInvitationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, invitation: Invitation) -> None:
        self._session.add(
            InvitationRow(
                id=invitation.id,
                tenant_id=invitation.tenant_id,
                email=invitation.email,
                role=invitation.role.value,
                token_hash=invitation.token_hash,
                invited_by=invitation.invited_by,
                created_at=invitation.created_at,
                expires_at=invitation.expires_at,
                accepted_at=invitation.accepted_at,
                accepted_by=invitation.accepted_by,
                revoked_at=invitation.revoked_at,
            )
        )

    async def get(self, invitation_id: InvitationId) -> Invitation | None:
        row = await self._session.get(InvitationRow, invitation_id)
        return _to_invitation(row) if row else None

    async def get_by_token_hash(self, tenant_id: TenantId, token_hash: str) -> Invitation | None:
        row = (
            await self._session.execute(
                select(InvitationRow).where(
                    InvitationRow.tenant_id == tenant_id,
                    InvitationRow.token_hash == token_hash,
                )
            )
        ).scalar_one_or_none()
        return _to_invitation(row) if row else None

    async def list_pending(self, tenant_id: TenantId) -> list[Invitation]:
        rows = (
            await self._session.execute(
                select(InvitationRow)
                .where(
                    InvitationRow.tenant_id == tenant_id,
                    InvitationRow.accepted_at.is_(None),
                    InvitationRow.revoked_at.is_(None),
                )
                .order_by(InvitationRow.created_at.desc())
            )
        ).scalars()
        return [_to_invitation(row) for row in rows]

    async def update(self, invitation: Invitation) -> None:
        row = await self._session.get(InvitationRow, invitation.id)
        if row is None:
            raise NotFoundError("invitation not found", detail={"id": str(invitation.id)})
        row.accepted_at = invitation.accepted_at
        row.accepted_by = invitation.accepted_by
        row.revoked_at = invitation.revoked_at
