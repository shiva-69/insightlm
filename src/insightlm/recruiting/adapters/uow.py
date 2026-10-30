"""Recruiting's concrete unit of work."""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from insightlm.governance.adapters.uow import SqlAlchemyGovernanceUnitOfWork
from insightlm.recruiting.adapters.repositories import (
    SqlAlchemyApplicationRepository,
    SqlAlchemyAssessmentJobRepository,
    SqlAlchemyAssessmentRepository,
    SqlAlchemyCandidateRepository,
    SqlAlchemyRequisitionRepository,
    SqlAlchemyRubricRepository,
    SqlAlchemyScorecardRepository,
    SqlAlchemySelfIdentificationRepository,
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
        self.assessment_jobs = SqlAlchemyAssessmentJobRepository(session)
        self.transitions = SqlAlchemyStageTransitionRepository(session)
        self.scorecards = SqlAlchemyScorecardRepository(session)
        self.self_identification = SqlAlchemySelfIdentificationRepository(session)
