"""Repository and unit-of-work ports for documents."""

from __future__ import annotations

from typing import Protocol

from insightlm.documents.domain.entities import (
    Document,
    DocumentVersion,
    ExtractedText,
    IngestionJob,
)
from insightlm.governance.ports.audit import AuditLog
from insightlm.shared.domain.ids import (
    DocumentId,
    DocumentVersionId,
    IngestionJobId,
    TenantId,
    UserId,
)
from insightlm.shared.ports.uow import UnitOfWork


class DocumentRepository(Protocol):
    async def add(self, document: Document) -> None: ...

    async def get(self, document_id: DocumentId) -> Document | None: ...

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 50, offset: int = 0
    ) -> list[Document]: ...


class DocumentVersionRepository(Protocol):
    async def add(self, version: DocumentVersion) -> None: ...

    async def get(self, version_id: DocumentVersionId) -> DocumentVersion | None: ...

    async def latest_for_document(self, document_id: DocumentId) -> DocumentVersion | None: ...

    async def find_by_checksum(self, tenant_id: TenantId, checksum: str) -> DocumentVersion | None:
        """Dedup. Re-uploading identical bytes must not reprocess them."""
        ...

    async def update(self, version: DocumentVersion) -> None: ...

    async def save_text(self, extracted: ExtractedText) -> None:
        """Persist the extracted text.

        Stored because citations resolve their quoted excerpt against it long after
        ingestion. Geometry is not stored here -- see :class:`ExtractedText`.
        """
        ...

    async def load_text(self, version_id: DocumentVersionId) -> ExtractedText | None: ...

    async def load_notes(self, version_id: DocumentVersionId) -> tuple[tuple[str, ...], bool]:
        """The parse warnings and whether OCR was used, without loading the text."""
        ...


class IngestionJobRepository(Protocol):
    async def add(self, job: IngestionJob) -> None: ...

    async def get(self, job_id: IngestionJobId) -> IngestionJob | None: ...

    async def for_version(self, version_id: DocumentVersionId) -> IngestionJob | None: ...

    async def update(self, job: IngestionJob) -> None: ...


class DocumentsUnitOfWork(UnitOfWork, Protocol):
    @property
    def documents(self) -> DocumentRepository: ...

    @property
    def versions(self) -> DocumentVersionRepository: ...

    @property
    def jobs(self) -> IngestionJobRepository: ...

    @property
    def audit(self) -> AuditLog: ...


class DocumentsUnitOfWorkFactory(Protocol):
    def __call__(
        self, *, tenant_id: TenantId | None = None, user_id: UserId | None = None
    ) -> DocumentsUnitOfWork: ...
