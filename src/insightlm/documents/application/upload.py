"""Uploading a document."""

from __future__ import annotations

from dataclasses import dataclass

from insightlm.documents.domain.entities import (
    PIPELINE_VERSION,
    Document,
    DocumentStatus,
    DocumentVersion,
    IngestionJob,
    MediaType,
    content_checksum,
)
from insightlm.documents.domain.storage import storage_key
from insightlm.documents.ports.repositories import DocumentsUnitOfWorkFactory
from insightlm.documents.ports.storage import ObjectStore
from insightlm.governance.domain.audit import AuditAction
from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.domain.ids import (
    new_document_id,
    new_document_version_id,
    new_ingestion_job_id,
    new_outbox_event_id,
)
from insightlm.shared.domain.policy import Actor, require
from insightlm.shared.domain.roles import Permission
from insightlm.shared.observability.logging import get_logger
from insightlm.shared.ports.clock import Clock
from insightlm.shared.ports.outbox import OutboxEvent

_logger = get_logger(__name__)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class UploadResult:
    document: Document
    version: DocumentVersion
    was_duplicate: bool
    """True when identical bytes already existed. The caller gets the existing version
    rather than a second copy, and no work is queued."""


class UploadDocument:
    def __init__(
        self,
        uow_factory: DocumentsUnitOfWorkFactory,
        store: ObjectStore,
        clock: Clock,
    ) -> None:
        self._uow_factory = uow_factory
        self._store = store
        self._clock = clock

    async def execute(
        self, actor: Actor, *, filename: str, payload: bytes, title: str | None = None
    ) -> UploadResult:
        require(actor, Permission.DOCUMENT_UPLOAD)

        if not payload:
            raise ValidationError("the uploaded file is empty")
        if len(payload) > MAX_UPLOAD_BYTES:
            raise ValidationError(
                "file exceeds the upload limit",
                detail={"limit_bytes": MAX_UPLOAD_BYTES, "size_bytes": len(payload)},
            )

        # Sniff from the filename, then let the parser reject what it cannot read.
        # Trusting a client-supplied content-type would be trusting the caller.
        media_type = MediaType.from_filename(filename)
        checksum = content_checksum(payload)
        now = self._clock.now()

        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            existing = await uow.versions.find_by_checksum(actor.tenant_id, checksum)
            if existing is not None:
                # Idempotent by content. Re-uploading the same bytes costs nothing and
                # queues nothing -- which is what makes a retried bulk upload safe.
                document = await uow.documents.get(existing.document_id)
                if document is not None:
                    _logger.info("upload_deduplicated", checksum=checksum[:12])
                    return UploadResult(document=document, version=existing, was_duplicate=True)

            document = Document(
                id=new_document_id(),
                tenant_id=actor.tenant_id,
                title=(title or filename).strip()[:500],
                media_type=media_type,
                uploaded_by=actor.user_id,
                created_at=now,
            )
            key = storage_key(actor.tenant_id, checksum)
            version = DocumentVersion(
                id=new_document_version_id(),
                document_id=document.id,
                tenant_id=actor.tenant_id,
                version=1,
                checksum=checksum,
                byte_size=len(payload),
                storage_key=key,
                status=DocumentStatus.PENDING,
                created_at=now,
            )
            job = IngestionJob(
                id=new_ingestion_job_id(),
                tenant_id=actor.tenant_id,
                document_version_id=version.id,
                pipeline_version=PIPELINE_VERSION,
                created_at=now,
            )

            # Bytes land in object storage before the row referencing them commits.
            # The reverse order can leave a row pointing at nothing; this order can at
            # worst orphan an object, which a lifecycle rule reclaims.
            await self._store.put(key, payload, content_type=media_type.value)

            await uow.documents.add(document)
            await uow.versions.add(version)
            await uow.jobs.add(job)

            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=AuditAction.DOCUMENT_UPLOADED,
                subject_type="document",
                subject_id=str(document.id),
                payload={
                    "title": document.title,
                    "media_type": media_type.value,
                    "byte_size": len(payload),
                    "checksum": checksum,
                },
            )
            uow.outbox.add(
                OutboxEvent(
                    id=new_outbox_event_id(),
                    tenant_id=actor.tenant_id,
                    event_type="document.ingest_requested",
                    payload={
                        "document_version_id": str(version.id),
                        "pipeline_version": PIPELINE_VERSION,
                    },
                    occurred_at=now,
                )
            )
            await uow.commit()

        return UploadResult(document=document, version=version, was_duplicate=False)
