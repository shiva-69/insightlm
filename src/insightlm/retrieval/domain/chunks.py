"""Chunks: the retrievable unit.

Chunking lives in `retrieval`, not `documents`, because chunk size, overlap, boundary
rules and contextual enrichment are **retrieval-quality decisions** -- every one of
them is an arm of the Phase 3 ablation (docs/03 SS5). `documents` owns bytes, versions
and the parsed text with its geometry; what that text is cut into, and how it is
indexed, is this module's business.

The practical consequence is that re-chunking a corpus under a new strategy never
touches the documents module, and comparing two strategies is a config change rather
than a migration.
"""

from __future__ import annotations

from dataclasses import dataclass

from insightlm.documents.domain.spans import TextSpan
from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.domain.ids import ChunkId, DocumentVersionId, TenantId


@dataclass(frozen=True, slots=True)
class Chunk:
    """A slice of a document version, anchored to where it physically sits.

    ``context_header`` is prepended to the text *before embedding* but is not part of
    the cited span. A chunk reading "Reduced p99 latency by 40%" is close to
    meaningless alone -- the section it came from is what makes it findable. Cheap at
    ingest, and a clean ablation arm later.
    """

    id: ChunkId
    document_version_id: DocumentVersionId
    tenant_id: TenantId
    ordinal: int
    text: str
    span: TextSpan
    section_path: tuple[str, ...] = ()
    context_header: str = ""

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValidationError("a chunk with no text is not retrievable")
        if self.ordinal < 0:
            raise ValidationError("ordinal must be non-negative")

    @property
    def embedding_text(self) -> str:
        """What is actually embedded: the contextual header plus the chunk."""
        return f"{self.context_header}\n\n{self.text}".strip() if self.context_header else self.text

    @property
    def section_label(self) -> str:
        return " / ".join(self.section_path) if self.section_path else "Document"
