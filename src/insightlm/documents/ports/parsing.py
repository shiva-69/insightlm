"""Document parsing.

Naive ``extract_text()`` is where most student RAG quietly dies: a two-column résumé
interleaves into nonsense, tables flatten into unretrievable soup, and a scanned PDF
yields an empty string that silently scores zero. The port exists so the parser can
be swapped and, more importantly, so parse quality becomes a *measured* property
rather than an assumption.
"""

from __future__ import annotations

from typing import Protocol

from insightlm.documents.domain.entities import MediaType, ParsedDocument


class DocumentParser(Protocol):
    def supports(self, media_type: MediaType) -> bool: ...

    async def parse(self, payload: bytes, *, media_type: MediaType) -> ParsedDocument:
        """Extract text with per-character geometry.

        Raises :class:`ValidationError` when the document cannot be read at all.
        Degraded-but-usable results (low OCR confidence, missing geometry) come back
        as ``warnings`` on the result rather than as an exception -- a recruiter needs
        to know a résumé scanned badly, not to have it disappear.
        """
        ...
