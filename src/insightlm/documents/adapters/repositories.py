"""SQLAlchemy repositories for documents."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from insightlm.documents.adapters.models import (
    DocumentRow,
    DocumentTextRow,
    DocumentVersionRow,
    IngestionJobRow,
)
from insightlm.documents.domain.entities import (
    Document,
    DocumentStatus,
    DocumentVersion,
    ExtractedText,
    IngestionJob,
    IngestionStep,
    MediaType,
    StepStatus,
)
from insightlm.shared.domain.errors import NotFoundError
from insightlm.shared.domain.ids import (
    DocumentId,
    DocumentVersionId,
    IngestionJobId,
    TenantId,
    UserId,
)


def _to_document(row: DocumentRow) -> Document:
    return Document(
        id=DocumentId(row.id),
        tenant_id=TenantId(row.tenant_id),
        title=row.title,
        media_type=MediaType(row.media_type),
        uploaded_by=UserId(row.uploaded_by),
        created_at=row.created_at,
    )


def _to_version(row: DocumentVersionRow) -> DocumentVersion:
    return DocumentVersion(
        id=DocumentVersionId(row.id),
        document_id=DocumentId(row.document_id),
        tenant_id=TenantId(row.tenant_id),
        version=row.version,
        checksum=row.checksum,
        byte_size=row.byte_size,
        storage_key=row.storage_key,
        status=DocumentStatus(row.status),
        created_at=row.created_at,
        page_count=row.page_count,
        char_count=row.char_count,
        failure_reason=row.failure_reason,
    )


def _to_job(row: IngestionJobRow) -> IngestionJob:
    return IngestionJob(
        id=IngestionJobId(row.id),
        tenant_id=TenantId(row.tenant_id),
        document_version_id=DocumentVersionId(row.document_version_id),
        pipeline_version=row.pipeline_version,
        created_at=row.created_at,
        completed_steps=tuple(IngestionStep(step) for step in row.completed_steps),
        status=StepStatus(row.status),
        attempts=row.attempts,
        last_error=row.last_error,
    )


class SqlAlchemyDocumentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, document: Document) -> None:
        self._session.add(
            DocumentRow(
                id=document.id,
                tenant_id=document.tenant_id,
                title=document.title,
                media_type=document.media_type.value,
                uploaded_by=document.uploaded_by,
                created_at=document.created_at,
            )
        )

    async def get(self, document_id: DocumentId) -> Document | None:
        row = await self._session.get(DocumentRow, document_id)
        return _to_document(row) if row else None

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 50, offset: int = 0
    ) -> list[Document]:
        rows = (
            await self._session.execute(
                select(DocumentRow)
                .where(DocumentRow.tenant_id == tenant_id)
                .order_by(DocumentRow.created_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
        return [_to_document(row) for row in rows]


class SqlAlchemyDocumentVersionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, version: DocumentVersion) -> None:
        self._session.add(
            DocumentVersionRow(
                id=version.id,
                document_id=version.document_id,
                tenant_id=version.tenant_id,
                version=version.version,
                checksum=version.checksum,
                byte_size=version.byte_size,
                storage_key=version.storage_key,
                status=version.status.value,
                page_count=version.page_count,
                char_count=version.char_count,
                failure_reason=version.failure_reason,
                created_at=version.created_at,
            )
        )

    async def get(self, version_id: DocumentVersionId) -> DocumentVersion | None:
        row = await self._session.get(DocumentVersionRow, version_id)
        return _to_version(row) if row else None

    async def latest_for_document(self, document_id: DocumentId) -> DocumentVersion | None:
        row = (
            await self._session.execute(
                select(DocumentVersionRow)
                .where(DocumentVersionRow.document_id == document_id)
                .order_by(DocumentVersionRow.version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return _to_version(row) if row else None

    async def find_by_checksum(self, tenant_id: TenantId, checksum: str) -> DocumentVersion | None:
        row = (
            await self._session.execute(
                select(DocumentVersionRow).where(
                    DocumentVersionRow.tenant_id == tenant_id,
                    DocumentVersionRow.checksum == checksum,
                )
            )
        ).scalar_one_or_none()
        return _to_version(row) if row else None

    async def update(self, version: DocumentVersion) -> None:
        row = await self._session.get(DocumentVersionRow, version.id)
        if row is None:
            raise NotFoundError("document version not found", detail={"id": str(version.id)})
        row.status = version.status.value
        row.page_count = version.page_count
        row.char_count = version.char_count
        row.failure_reason = version.failure_reason

    async def save_text(self, extracted: ExtractedText) -> None:
        existing = await self._session.get(DocumentTextRow, extracted.document_version_id)
        version = await self._session.get(DocumentVersionRow, extracted.document_version_id)
        if version is None:
            raise NotFoundError("document version not found")

        if existing is None:
            self._session.add(
                DocumentTextRow(
                    document_version_id=extracted.document_version_id,
                    tenant_id=version.tenant_id,
                    body=extracted.text,
                    page_count=extracted.page_count,
                    warnings=list(extracted.warnings),
                )
            )
        else:
            # Re-ingestion under a new pipeline version overwrites rather than
            # accumulating; the version's bytes are immutable, so the text derived
            # from them is a cache, not history.
            existing.body = extracted.text
            existing.page_count = extracted.page_count
            existing.warnings = list(extracted.warnings)

    async def load_text(self, version_id: DocumentVersionId) -> ExtractedText | None:
        row = await self._session.get(DocumentTextRow, version_id)
        if row is None:
            return None
        return ExtractedText(
            document_version_id=DocumentVersionId(row.document_version_id),
            text=row.body,
            page_count=row.page_count,
            warnings=tuple(row.warnings),
        )


class SqlAlchemyIngestionJobRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, job: IngestionJob) -> None:
        self._session.add(
            IngestionJobRow(
                id=job.id,
                tenant_id=job.tenant_id,
                document_version_id=job.document_version_id,
                pipeline_version=job.pipeline_version,
                completed_steps=[step.value for step in job.completed_steps],
                status=job.status.value,
                attempts=job.attempts,
                last_error=job.last_error,
                created_at=job.created_at,
            )
        )

    async def get(self, job_id: IngestionJobId) -> IngestionJob | None:
        row = await self._session.get(IngestionJobRow, job_id)
        return _to_job(row) if row else None

    async def for_version(self, version_id: DocumentVersionId) -> IngestionJob | None:
        row = (
            await self._session.execute(
                select(IngestionJobRow)
                .where(IngestionJobRow.document_version_id == version_id)
                .order_by(IngestionJobRow.pipeline_version.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        return _to_job(row) if row else None

    async def update(self, job: IngestionJob) -> None:
        row = await self._session.get(IngestionJobRow, job.id)
        if row is None:
            raise NotFoundError("ingestion job not found", detail={"id": str(job.id)})
        row.completed_steps = [step.value for step in job.completed_steps]
        row.status = job.status.value
        row.attempts = job.attempts
        row.last_error = job.last_error
