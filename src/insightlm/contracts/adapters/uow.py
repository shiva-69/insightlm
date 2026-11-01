"""Contract review's concrete unit of work: governance's, plus its own repositories."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from insightlm.contracts.adapters.repositories import (
    SqlAlchemyContractReviewRepository,
    SqlAlchemyPlaybookRepository,
)
from insightlm.governance.adapters.uow import SqlAlchemyGovernanceUnitOfWork


class SqlAlchemyContractsUnitOfWork(SqlAlchemyGovernanceUnitOfWork):
    def _build_repositories(self, session: AsyncSession) -> None:
        super()._build_repositories(session)
        self.playbooks = SqlAlchemyPlaybookRepository(session)
        self.reviews = SqlAlchemyContractReviewRepository(session)
