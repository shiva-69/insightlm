"""Identity's concrete unit of work."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.governance.adapters.uow import SqlAlchemyGovernanceUnitOfWork
from insightlm.identity.adapters.repositories import (
    SqlAlchemyInvitationRepository,
    SqlAlchemyMembershipRepository,
    SqlAlchemyTenantRepository,
    SqlAlchemyUserRepository,
)
from insightlm.shared.domain.ids import TenantId, UserId
from insightlm.shared.ports.clock import Clock


class SqlAlchemyIdentityUnitOfWork(SqlAlchemyGovernanceUnitOfWork):
    """Satisfies ``IdentityUnitOfWork`` structurally -- it never inherits from the
    Protocol, which is what keeps the dependency arrow pointing inward.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        *,
        tenant_id: TenantId | None = None,
        user_id: UserId | None = None,
    ) -> None:
        super().__init__(session_factory, clock, tenant_id=tenant_id, user_id=user_id)

    def _build_repositories(self, session: AsyncSession) -> None:
        super()._build_repositories(session)
        self.tenants = SqlAlchemyTenantRepository(session)
        self.users = SqlAlchemyUserRepository(session)
        self.memberships = SqlAlchemyMembershipRepository(session)
        self.invitations = SqlAlchemyInvitationRepository(session)
