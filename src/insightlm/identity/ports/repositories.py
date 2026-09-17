"""Repository ports for the identity module.

Repositories persist and retrieve aggregates. They never open or commit a
transaction -- that belongs to the unit of work.
"""

from __future__ import annotations

from typing import Protocol

from insightlm.identity.domain.entities import Invitation, Membership, Tenant, User
from insightlm.shared.domain.ids import InvitationId, MembershipId, TenantId, UserId


class TenantRepository(Protocol):
    async def add(self, tenant: Tenant) -> None: ...

    async def get(self, tenant_id: TenantId) -> Tenant | None: ...

    async def get_by_slug(self, slug: str) -> Tenant | None: ...


class UserRepository(Protocol):
    async def add(self, user: User) -> None: ...

    async def get(self, user_id: UserId) -> User | None: ...

    async def get_by_email(self, email: str) -> User | None:
        """Look up by *normalised* email. Callers must normalise first."""
        ...

    async def update(self, user: User) -> None: ...


class MembershipRepository(Protocol):
    async def add(self, membership: Membership) -> None: ...

    async def get(self, membership_id: MembershipId) -> Membership | None: ...

    async def get_for_user(self, tenant_id: TenantId, user_id: UserId) -> Membership | None: ...

    async def list_for_tenant(self, tenant_id: TenantId) -> list[Membership]: ...

    async def list_for_user(self, user_id: UserId) -> list[Membership]: ...

    async def update(self, membership: Membership) -> None: ...


class InvitationRepository(Protocol):
    async def add(self, invitation: Invitation) -> None: ...

    async def get(self, invitation_id: InvitationId) -> Invitation | None: ...

    async def get_by_token_hash(
        self, tenant_id: TenantId, token_hash: str
    ) -> Invitation | None: ...

    async def list_pending(self, tenant_id: TenantId) -> list[Invitation]: ...

    async def update(self, invitation: Invitation) -> None: ...
