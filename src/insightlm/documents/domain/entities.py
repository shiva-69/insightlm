"""Document, version, chunk, and the ingestion job that produces them."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Self

from insightlm.documents.domain.findings import ContentFinding
from insightlm.documents.domain.spans import PageRect
from insightlm.shared.domain.errors import ConflictError, ValidationError
from insightlm.shared.domain.ids import (
    DocumentId,
    DocumentVersionId,
    IngestionJobId,
    TenantId,
    UserId,
)

PIPELINE_VERSION = 1
"""Bumped when parsing, chunking or embedding changes in a way that invalidates
existing derived data. It is part of the ingestion idempotency key, so raising it
reprocesses every document deliberately rather than by accident."""


class MediaType(StrEnum):
    PDF = "application/pdf"
    DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    TEXT = "text/plain"
    MARKDOWN = "text/markdown"

    @classmethod
    def from_filename(cls, filename: str) -> Self:
        suffix = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
        mapping = {
            "pdf": cls.PDF,
            "docx": cls.DOCX,
            "txt": cls.TEXT,
            "md": cls.MARKDOWN,
            "markdown": cls.MARKDOWN,
        }
        if suffix not in mapping:
            raise ValidationError(
                "unsupported file type",
                detail={"filename": filename, "supported": sorted(mapping)},
            )
        return mapping[suffix]


class DocumentStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    READY = "ready"
    FAILED = "failed"


class IngestionStep(StrEnum):
    """The pipeline, as data.

    Steps are persisted as they complete, so a retry resumes from the last good one
    rather than re-parsing a 300-page PDF to redo an embedding call.
    """

    PARSE = "parse"
    SANITISE = "sanitise"
    CHUNK = "chunk"
    EMBED = "embed"
    INDEX = "index"

    @classmethod
    def ordered(cls) -> tuple[IngestionStep, ...]:
        return (cls.PARSE, cls.SANITISE, cls.CHUNK, cls.EMBED, cls.INDEX)


class StepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


def content_checksum(payload: bytes) -> str:
    """SHA-256 of the raw bytes. Identity for dedup and for the idempotency key."""
    return hashlib.sha256(payload).hexdigest()


@dataclass(frozen=True, slots=True)
class Document:
    """A logical document. Its content lives on versions."""

    id: DocumentId
    tenant_id: TenantId
    title: str
    media_type: MediaType
    uploaded_by: UserId
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise ValidationError("document title must not be blank")


@dataclass(frozen=True, slots=True)
class DocumentVersion:
    """One immutable revision of a document's bytes.

    Versioned rather than overwritten because an assessment or an answer cites *this*
    text. A candidate sending an updated résumé must not silently invalidate a
    citation that pointed at the old one -- the old version stays, and the answer
    stays explainable.
    """

    id: DocumentVersionId
    document_id: DocumentId
    tenant_id: TenantId
    version: int
    checksum: str
    byte_size: int
    storage_key: str
    status: DocumentStatus
    created_at: datetime
    page_count: int | None = None
    char_count: int | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValidationError("version numbers start at 1")
        if len(self.checksum) != 64:
            raise ValidationError("checksum must be a sha-256 hex digest")
        if self.byte_size <= 0:
            raise ValidationError("an empty document has nothing to ingest")

    def processing(self) -> Self:
        return replace(self, status=DocumentStatus.PROCESSING)

    def ready(self, *, page_count: int, char_count: int) -> Self:
        return replace(
            self,
            status=DocumentStatus.READY,
            page_count=page_count,
            char_count=char_count,
            failure_reason=None,
        )

    def failed(self, reason: str) -> Self:
        return replace(self, status=DocumentStatus.FAILED, failure_reason=reason[:1000])


@dataclass(frozen=True, slots=True)
class IngestionJob:
    """Tracks one document version through the pipeline."""

    id: IngestionJobId
    tenant_id: TenantId
    document_version_id: DocumentVersionId
    pipeline_version: int
    created_at: datetime
    completed_steps: tuple[IngestionStep, ...] = ()
    status: StepStatus = StepStatus.PENDING
    attempts: int = 0
    last_error: str | None = None

    @property
    def next_step(self) -> IngestionStep | None:
        """The first step not yet completed, or None when the pipeline is done."""
        for step in IngestionStep.ordered():
            if step not in self.completed_steps:
                return step
        return None

    @property
    def is_complete(self) -> bool:
        return self.next_step is None

    def step_completed(self, step: IngestionStep) -> Self:
        if step in self.completed_steps:
            raise ConflictError(f"step '{step}' has already completed")
        return replace(
            self,
            completed_steps=(*self.completed_steps, step),
            status=StepStatus.RUNNING,
            last_error=None,
        )

    def succeeded(self) -> Self:
        if not self.is_complete:
            raise ConflictError(
                "cannot succeed with steps outstanding",
                detail={"next_step": self.next_step},
            )
        return replace(self, status=StepStatus.SUCCEEDED, last_error=None)

    def failed(self, reason: str) -> Self:
        """Record the failure but keep ``completed_steps`` -- that is what makes the
        retry resumable rather than a restart."""
        return replace(
            self,
            status=StepStatus.FAILED,
            attempts=self.attempts + 1,
            last_error=reason[:1000],
        )


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """What a parser produces: the extracted text, plus geometry for every character.

    Index-aligned by contract -- ``char_rects[i]`` is the rectangle for ``text[i]``.
    That alignment is the whole reason citations can be drawn: the string we chunk and
    embed is the same string whose offsets we later resolve to a highlight. A parser
    that returns them out of step breaks the citation contract silently, so
    :meth:`__post_init__` refuses to construct one.
    """

    text: str
    char_rects: tuple[PageRect | None, ...]
    page_count: int
    section_offsets: tuple[tuple[int, str], ...] = ()
    """``(char_offset, heading)`` pairs marking where each detected section starts."""
    ocr_used: bool = False
    ocr_confidence: float | None = None
    warnings: tuple[str, ...] = ()
    findings: tuple[ContentFinding, ...] = ()
    """Concealed or instruction-like content found while parsing. See `findings`."""

    def __post_init__(self) -> None:
        if len(self.char_rects) != len(self.text):
            raise ValidationError(
                "parsed text and character geometry are not index-aligned",
                detail={"text_len": len(self.text), "rects_len": len(self.char_rects)},
            )

    def rects_for(self, char_start: int, char_end: int) -> list[PageRect]:
        """Geometry for a character range, skipping whitespace and unmapped chars."""
        return [
            rect
            for index in range(max(0, char_start), min(char_end, len(self.text)))
            if (rect := self.char_rects[index]) is not None and self.text[index].strip()
        ]

    def section_at(self, offset: int) -> str | None:
        """The heading in force at a character offset."""
        current: str | None = None
        for start, heading in self.section_offsets:
            if start <= offset:
                current = heading
            else:
                break
        return current


@dataclass(frozen=True, slots=True)
class ExtractedText:
    """The persisted result of parsing: text, page count, warnings.

    Deliberately *not* the per-character geometry. Storing a rectangle per character
    would be tens of megabytes of JSONB for a long PDF, for data that is only needed
    once -- at chunking time -- and is cheap to recreate. What survives is the merged
    per-chunk geometry on the chunk itself, which is all a highlight needs.

    Re-chunking therefore re-parses from the stored bytes rather than from this. That
    is the honest trade: a rare, explicit operation pays for a storage cost every
    document would otherwise carry.
    """

    document_version_id: DocumentVersionId
    text: str
    page_count: int
    warnings: tuple[str, ...] = ()
    findings: tuple[ContentFinding, ...] = ()
    ocr_used: bool = False
    """Some of ``text`` was recognised from a scanned image rather than read from the file."""

    def excerpt(self, char_start: int, char_end: int, *, padding: int = 0) -> str:
        start = max(0, char_start - padding)
        end = min(len(self.text), char_end + padding)
        return self.text[start:end]
