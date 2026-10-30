"""Typed identifiers.

`NewType` over `UUID` gives compile-time safety with zero runtime cost: passing a
``UserId`` where a ``TenantId`` is expected is a mypy error, but the value is still
just a UUID at runtime.
"""

from __future__ import annotations

from typing import NewType
from uuid import UUID, uuid4

TenantId = NewType("TenantId", UUID)
UserId = NewType("UserId", UUID)
MembershipId = NewType("MembershipId", UUID)
InvitationId = NewType("InvitationId", UUID)
AuditEntryId = NewType("AuditEntryId", UUID)
OutboxEventId = NewType("OutboxEventId", UUID)
DocumentId = NewType("DocumentId", UUID)
DocumentVersionId = NewType("DocumentVersionId", UUID)
ChunkId = NewType("ChunkId", UUID)
IngestionJobId = NewType("IngestionJobId", UUID)
ConversationId = NewType("ConversationId", UUID)
MessageId = NewType("MessageId", UUID)
RequisitionId = NewType("RequisitionId", UUID)
RubricId = NewType("RubricId", UUID)
CriterionId = NewType("CriterionId", UUID)
CandidateId = NewType("CandidateId", UUID)
ApplicationId = NewType("ApplicationId", UUID)
AssessmentId = NewType("AssessmentId", UUID)
AssessmentJobId = NewType("AssessmentJobId", UUID)
ScorecardId = NewType("ScorecardId", UUID)
CorrelationId = NewType("CorrelationId", str)


def new_tenant_id() -> TenantId:
    return TenantId(uuid4())


def new_user_id() -> UserId:
    return UserId(uuid4())


def new_membership_id() -> MembershipId:
    return MembershipId(uuid4())


def new_invitation_id() -> InvitationId:
    return InvitationId(uuid4())


def new_audit_entry_id() -> AuditEntryId:
    return AuditEntryId(uuid4())


def new_outbox_event_id() -> OutboxEventId:
    return OutboxEventId(uuid4())


def new_document_id() -> DocumentId:
    return DocumentId(uuid4())


def new_document_version_id() -> DocumentVersionId:
    return DocumentVersionId(uuid4())


def new_chunk_id() -> ChunkId:
    return ChunkId(uuid4())


def new_ingestion_job_id() -> IngestionJobId:
    return IngestionJobId(uuid4())


def new_conversation_id() -> ConversationId:
    return ConversationId(uuid4())


def new_message_id() -> MessageId:
    return MessageId(uuid4())


def new_requisition_id() -> RequisitionId:
    return RequisitionId(uuid4())


def new_rubric_id() -> RubricId:
    return RubricId(uuid4())


def new_criterion_id() -> CriterionId:
    return CriterionId(uuid4())


def new_candidate_id() -> CandidateId:
    return CandidateId(uuid4())


def new_application_id() -> ApplicationId:
    return ApplicationId(uuid4())


def new_assessment_id() -> AssessmentId:
    return AssessmentId(uuid4())


def new_assessment_job_id() -> AssessmentJobId:
    return AssessmentJobId(uuid4())


def new_scorecard_id() -> ScorecardId:
    return ScorecardId(uuid4())
