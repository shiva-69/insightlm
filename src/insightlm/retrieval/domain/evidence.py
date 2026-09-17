"""Evidence: what retrieval returns, and the only thing the rest of the system sees.

These types are the vocabulary of `EvidencePort` -- the one seam between the document
engine and everything built on it. Nothing here mentions embeddings, chunking
strategy, rank fusion or vector SQL, which is what lets `recruiting` (Phase 4) depend
on retrieval without knowing how retrieval works.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from insightlm.documents.domain.spans import TextSpan
from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.domain.ids import ChunkId, DocumentId, DocumentVersionId


class RetrievalStrategy(StrEnum):
    """Which retrieval arms ran. Recorded on every answer so a result can be
    reproduced, and so the Phase 3 ablation is a config sweep rather than a rewrite."""

    DENSE = "dense"
    LEXICAL = "lexical"
    HYBRID_RRF = "hybrid_rrf"
    HYBRID_RRF_RERANK = "hybrid_rrf_rerank"


@dataclass(frozen=True, slots=True)
class EvidenceQuery:
    """A request for evidence, scoped to a corpus.

    ``document_ids`` narrows the search -- Phase 4 uses it to ask about one
    candidate's documents rather than the whole tenant.
    """

    text: str
    top_k: int = 8
    document_ids: tuple[DocumentId, ...] | None = None
    strategy: RetrievalStrategy = RetrievalStrategy.DENSE
    min_score: float = 0.0

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValidationError("an empty query retrieves nothing useful")
        if not 1 <= self.top_k <= 100:
            raise ValidationError("top_k must be between 1 and 100")


@dataclass(frozen=True, slots=True)
class RetrievedSpan:
    """One piece of evidence: the text, where it came from, and how it scored.

    ``label`` is the stable handle the model cites (``C1``, ``C2``, ...). The model is
    never asked to reproduce a document name or a page number -- it gets those subtly
    wrong, and a wrong citation is worse than no citation. It cites a tag, and the
    tag resolves here.
    """

    label: str
    chunk_id: ChunkId
    document_id: DocumentId
    document_version_id: DocumentVersionId
    document_title: str
    text: str
    span: TextSpan
    score: float
    section_label: str = "Document"

    @property
    def page(self) -> int:
        return self.span.first_page


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    """The ranked evidence for one query, plus how it was produced."""

    query: EvidenceQuery
    spans: tuple[RetrievedSpan, ...]
    strategy: RetrievalStrategy
    config_hash: str
    candidates_considered: int = 0

    @property
    def is_empty(self) -> bool:
        return not self.spans

    def by_label(self) -> dict[str, RetrievedSpan]:
        return {span.label: span for span in self.spans}

    def as_context(self) -> str:
        """Render for a prompt.

        Each block is delimited and tagged. The delimiters also mark this as untrusted
        data -- document text is content, never instruction. That separation is
        necessary but not sufficient against injection; the Phase 6 defences layer on
        top of it (see docs/03 SS6).
        """
        blocks = []
        for span in self.spans:
            blocks.append(
                f'<source id="{span.label}" title="{span.document_title}" '
                f'section="{span.section_label}" page="{span.page + 1}">\n'
                f"{span.text}\n"
                f"</source>"
            )
        return "\n\n".join(blocks)


def label_for(index: int) -> str:
    """Stable, short, and unambiguous in a prompt."""
    return f"C{index + 1}"
