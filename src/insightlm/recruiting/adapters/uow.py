"""Recruiting's concrete unit of work."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from insightlm.governance.adapters.uow import SqlAlchemyGovernanceUnitOfWork
from insightlm.recruiting.adapters.repositories import (
    SqlAlchemyApplicationRepository,
    SqlAlchemyAssessmentRepository,
    SqlAlchemyCandidateRepository,
    SqlAlchemyRequisitionRepository,
    SqlAlchemyRubricRepository,
    SqlAlchemyScorecardRepository,
    SqlAlchemyStageTransitionRepository,
)


class SqlAlchemyRecruitingUnitOfWork(SqlAlchemyGovernanceUnitOfWork):
    def _build_repositories(self, session: AsyncSession) -> None:
        super()._build_repositories(session)
        self.requisitions = SqlAlchemyRequisitionRepository(session)
        self.rubrics = SqlAlchemyRubricRepository(session)
        self.candidates = SqlAlchemyCandidateRepository(session)
        self.applications = SqlAlchemyApplicationRepository(session)
        self.assessments = SqlAlchemyAssessmentRepository(session)
        self.transitions = SqlAlchemyStageTransitionRepository(session)
        self.scorecards = SqlAlchemyScorecardRepository(session)
