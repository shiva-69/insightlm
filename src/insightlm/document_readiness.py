"""Recruiting's view of whether a candidate's documents have been read yet.

Composition, not domain: it is the one place that may look at the documents module on
recruiting's behalf. Recruiting asks a yes/no/not-yet question through its own port
(``recruiting.ports.documents``) and never learns what ingestion is.
"""

from __future__ import annotations

from insightlm.documents.domain.entities import DocumentStatus
from insightlm.documents.ports.repositories import DocumentsUnitOfWorkFactory
from insightlm.recruiting.domain.assessment_job import Readiness
from insightlm.shared.domain.ids import DocumentId, TenantId


class DocumentReadinessAdapter:
    def __init__(self, documents_uow: DocumentsUnitOfWorkFactory) -> None:
        self._documents_uow = documents_uow

    async def readiness(
        self, tenant_id: TenantId, document_ids: tuple[DocumentId, ...]
    ) -> Readiness:
        statuses: list[DocumentStatus] = []
        async with self._documents_uow(tenant_id=tenant_id) as uow:
            for document_id in document_ids:
                version = await uow.versions.latest_for_document(document_id)
                # A document that has vanished cannot be assessed; treat it as unreadable
                # rather than waiting for it to appear.
                statuses.append(version.status if version else DocumentStatus.FAILED)
        if DocumentStatus.FAILED in statuses:
            return Readiness.FAILED
        if any(s is not DocumentStatus.READY for s in statuses):
            return Readiness.PROCESSING
        return Readiness.READY
