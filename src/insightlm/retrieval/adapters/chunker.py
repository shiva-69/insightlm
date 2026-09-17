"""Structure-aware chunking.

Fixed-width chunking is the default that quietly caps retrieval quality: it splits
mid-sentence, severs a bullet from its heading, and cuts tables in half. This chunker
respects the document's own structure first and only then falls back to a size budget.

Three rules, each of which is an ablation arm in Phase 3:

1. **Sections are hard boundaries.** A chunk never spans two headings. "EXPERIENCE"
   and "EDUCATION" are different topics and blending them makes both less findable.
2. **Splits prefer paragraph, then sentence, then whitespace.** Cutting mid-word
   produces a chunk that embeds as noise.
3. **Overlap carries context across a split**, so a fact sitting on a boundary is
   retrievable from either side.

Geometry survives all of it -- each chunk keeps the character range it came from and
the rectangles that cover it, which is what makes the citation clickable.
"""

from __future__ import annotations

import re

from insightlm.documents.domain.entities import ParsedDocument
from insightlm.documents.domain.spans import TextSpan, merge_rects_by_line
from insightlm.retrieval.domain.chunks import Chunk
from insightlm.shared.domain.ids import (
    DocumentVersionId,
    TenantId,
    new_chunk_id,
)

CHARS_PER_TOKEN = 4
"""A rough proxy. Real tokenisation is a Phase 3 concern -- the budget only has to be
approximately right, and being wrong by 10% costs nothing measurable."""

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


class StructureAwareChunker:
    def __init__(
        self,
        *,
        target_tokens: int = 320,
        overlap_tokens: int = 48,
        min_tokens: int = 24,
    ) -> None:
        self._target = target_tokens * CHARS_PER_TOKEN
        self._overlap = overlap_tokens * CHARS_PER_TOKEN
        self._min = min_tokens * CHARS_PER_TOKEN

    def chunk(
        self,
        parsed: ParsedDocument,
        *,
        tenant_id: TenantId,
        document_version_id: DocumentVersionId,
    ) -> list[Chunk]:
        chunks: list[Chunk] = []
        ordinal = 0

        for start, end, heading in self._sections(parsed):
            body = parsed.text[start:end]
            if not body.strip():
                continue

            for piece_start, piece_end in self._split(body):
                absolute_start = start + piece_start
                absolute_end = start + piece_end
                text = parsed.text[absolute_start:absolute_end].strip()
                if not text:
                    continue

                # Re-anchor after stripping, so the span covers the text we kept and
                # not the whitespace we discarded -- otherwise the highlight is offset.
                leading = parsed.text[absolute_start:absolute_end].index(text)
                span_start = absolute_start + leading
                span_end = span_start + len(text)

                rects = merge_rects_by_line(parsed.rects_for(span_start, span_end))
                section_path = (heading,) if heading else ()

                chunks.append(
                    Chunk(
                        id=new_chunk_id(),
                        document_version_id=document_version_id,
                        tenant_id=tenant_id,
                        ordinal=ordinal,
                        text=text,
                        span=TextSpan(char_start=span_start, char_end=span_end, rects=rects),
                        section_path=section_path,
                        context_header=heading or "",
                    )
                )
                ordinal += 1

        return chunks

    def _sections(self, parsed: ParsedDocument) -> list[tuple[int, int, str | None]]:
        """Section ranges as ``(start, end, heading)``, covering the whole document."""
        if not parsed.section_offsets:
            return [(0, len(parsed.text), None)]

        sections: list[tuple[int, int, str | None]] = []
        first_start = parsed.section_offsets[0][0]
        if first_start > 0:
            sections.append((0, first_start, None))

        for index, (start, heading) in enumerate(parsed.section_offsets):
            is_last = index == len(parsed.section_offsets) - 1
            end = len(parsed.text) if is_last else parsed.section_offsets[index + 1][0]
            sections.append((start, end, heading))

        return sections

    def _split(self, body: str) -> list[tuple[int, int]]:
        """Offsets of each piece within ``body``, honouring the size budget."""
        if len(body) <= self._target:
            return [(0, len(body))]

        pieces: list[tuple[int, int]] = []
        cursor = 0

        while cursor < len(body):
            limit = min(cursor + self._target, len(body))
            if limit >= len(body):
                pieces.append((cursor, len(body)))
                break

            boundary = self._best_boundary(body, cursor, limit)
            pieces.append((cursor, boundary))

            # Step back by the overlap, but always make forward progress -- a boundary
            # inside the overlap window would otherwise loop forever.
            next_cursor = max(boundary - self._overlap, cursor + 1)
            cursor = next_cursor if next_cursor < boundary else boundary

        # A short trailing fragment is noise on its own; fold it into its predecessor.
        if len(pieces) > 1 and pieces[-1][1] - pieces[-1][0] < self._min:
            start, _ = pieces[-2]
            pieces = [*pieces[:-2], (start, pieces[-1][1])]

        return pieces

    def _best_boundary(self, body: str, start: int, limit: int) -> int:
        """Prefer a paragraph break, then a sentence end, then whitespace."""
        window = body[start:limit]
        floor = int(len(window) * 0.5)  # do not produce a runt to find a nice boundary

        breaks = [m.end() for m in _PARAGRAPH_BREAK.finditer(window) if m.end() >= floor]
        if breaks:
            return start + breaks[-1]

        sentences = [m.end() for m in _SENTENCE_END.finditer(window) if m.end() >= floor]
        if sentences:
            return start + sentences[-1]

        space = window.rfind(" ", floor)
        if space > 0:
            return start + space + 1

        return limit
