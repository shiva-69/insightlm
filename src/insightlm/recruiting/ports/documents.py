"""Whether a candidate's documents are ready to be assessed.

Recruiting cannot see ingestion -- it only reads evidence through ``EvidencePort``. But
an assessment queued the moment a CV is uploaded would otherwise run against a document
that has no passages yet, and every criterion would come back "absent". This port lets
the job wait for the document without recruiting knowing how documents are processed.
"""

from __future__ import annotations

from typing import Protocol

from insightlm.recruiting.domain.assessment_job import Readiness
from insightlm.shared.domain.ids import DocumentId, TenantId


class DocumentReadiness(Protocol):
    async def readiness(
        self, tenant_id: TenantId, document_ids: tuple[DocumentId, ...]
    ) -> Readiness:
        """FAILED if any document failed, PROCESSING if any is unfinished, else READY."""
        ...
