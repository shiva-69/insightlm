"""Governance's concrete unit of work.

Identity's unit of work extends this one, so the inheritance chain mirrors the module
layering: everything audits, and audit depends on nothing above it.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.governance.adapters.audit import SqlAlchemyAuditLog
from insightlm.shared.adapters.db.uow import SqlAlchemyUnitOfWork
from insightlm.shared.domain.ids import TenantId, UserId
from insightlm.shared.ports.clock import Clock


class SqlAlchemyGovernanceUnitOfWork(SqlAlchemyUnitOfWork):
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        clock: Clock,
        *,
        tenant_id: TenantId | None = None,
        user_id: UserId | None = None,
    ) -> None:
        super().__init__(session_factory, tenant_id=tenant_id, user_id=user_id)
        self._clock = clock

    def _build_repositories(self, session: AsyncSession) -> None:
        self.audit = SqlAlchemyAuditLog(session, self._clock)
