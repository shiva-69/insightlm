"""The audit actions recruiting records."""

from __future__ import annotations

from insightlm.governance.domain.audit import AuditAction


class RecruitingAudit:
    REQUISITION_CREATED = AuditAction("requisition.created")
    RUBRIC_PROPOSED = AuditAction("rubric.proposed")
    RUBRIC_APPROVED = AuditAction("rubric.approved")
    CANDIDATE_ADDED = AuditAction("candidate.added")
    ASSESSMENT_REQUESTED = AuditAction("assessment.requested")
    ASSESSMENT_CREATED = AuditAction("assessment.created")
    STAGE_TRANSITIONED = AuditAction("application.stage_transitioned")
    INTERVIEWER_ASSIGNED = AuditAction("application.interviewer_assigned")
    SCORECARD_SUBMITTED = AuditAction("scorecard.submitted")
    CANDIDATE_DATA_ACCESSED = AuditAction("candidate.data_accessed")
