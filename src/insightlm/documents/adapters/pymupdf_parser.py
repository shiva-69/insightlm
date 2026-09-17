"""PDF, DOCX and plain-text parsing with character-level geometry.

The contract this adapter must not break: ``parsed.char_rects[i]`` is the rectangle
for ``parsed.text[i]``. Text and geometry are therefore built in a single pass -- the
tempting shortcut of extracting text first and locating it afterwards drifts the
moment ligatures, hyphenation or whitespace normalisation enter the picture, and the
drift is invisible until a highlight lands on the wrong sentence.

Layout handling is deliberate rather than incidental. PyMuPDF's ``rawdict`` walks
blocks in reading order, so a two-column résumé yields each column as its own block
instead of interleaving the two -- the failure that makes naive extraction produce
plausible-looking nonsense. Verified in `spikes/pdf_geometry_spike.py`.
"""

from __future__ import annotations

import asyncio
import io
import statistics
from typing import Any

import pymupdf

from insightlm.documents.domain.entities import MediaType, ParsedDocument
from insightlm.documents.domain.spans import PageRect
from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.observability.logging import get_logger

_logger = get_logger(__name__)

_MIN_CHARS_PER_PAGE = 60
"""Below this, a PDF is almost certainly scanned images. Phase 6 adds the OCR
fallback; Phase 1 records the warning so the document is visibly degraded rather
than silently empty."""

_HEADING_SIZE_RATIO = 1.15
"""A span whose font is this much larger than the document's median is treated as a
heading. Ratio rather than an absolute size, because point sizes vary by template."""


class PyMuPdfParser:
    """Parses PDF natively; DOCX and text without geometry."""

    def supports(self, media_type: MediaType) -> bool:
        return media_type in {
            MediaType.PDF,
            MediaType.DOCX,
            MediaType.TEXT,
            MediaType.MARKDOWN,
        }

    async def parse(self, payload: bytes, *, media_type: MediaType) -> ParsedDocument:
        if not payload:
            raise ValidationError("cannot parse an empty file")
        # Parsing is CPU-bound and PyMuPDF releases no event loop. Running it on the
        # loop would stall every other request on the worker.
        return await asyncio.to_thread(self._parse_sync, payload, media_type)

    def _parse_sync(self, payload: bytes, media_type: MediaType) -> ParsedDocument:
        if media_type is MediaType.PDF:
            return self._parse_pdf(payload)
        if media_type is MediaType.DOCX:
            return self._parse_docx(payload)
        return self._parse_text(payload.decode("utf-8", errors="replace"))

    # ------------------------------------------------------------------ PDF
    def _parse_pdf(self, payload: bytes) -> ParsedDocument:
        try:
            doc = pymupdf.open(stream=payload, filetype="pdf")
        except Exception as exc:
            raise ValidationError(
                "this PDF could not be opened; it may be corrupt or encrypted"
            ) from exc

        try:
            font_sizes = self._collect_font_sizes(doc)
            median_size = statistics.median(font_sizes) if font_sizes else 10.0

            pieces: list[str] = []
            rects: list[PageRect | None] = []
            sections: list[tuple[int, str]] = []
            cursor = 0

            for page_number in range(doc.page_count):
                page = doc.load_page(page_number)
                for block in page.get_text("rawdict")["blocks"]:
                    if block.get("type") != 0:  # 0 = text; 1 = image
                        continue
                    for line in block["lines"]:
                        line_text, line_rects, max_size = self._read_line(line, page_number)
                        if not line_text.strip():
                            continue

                        if self._looks_like_heading(line_text, max_size, median_size):
                            sections.append((cursor, line_text.strip()))

                        pieces.append(line_text)
                        rects.extend(line_rects)
                        cursor += len(line_text)

                        pieces.append("\n")
                        rects.append(None)
                        cursor += 1

                # Page break, so chunk boundaries can respect pages later.
                pieces.append("\n")
                rects.append(None)
                cursor += 1

            text = "".join(pieces)
            warnings: list[str] = []
            page_count = doc.page_count

            if page_count and len(text.strip()) < _MIN_CHARS_PER_PAGE * page_count:
                warnings.append(
                    "very little extractable text -- this document is probably scanned "
                    "and needs OCR"
                )
                _logger.warning(
                    "document_probably_scanned", pages=page_count, chars=len(text.strip())
                )

            return ParsedDocument(
                text=text,
                char_rects=tuple(rects),
                page_count=page_count,
                section_offsets=tuple(sections),
                warnings=tuple(warnings),
            )
        finally:
            doc.close()

    def _collect_font_sizes(self, doc: pymupdf.Document) -> list[float]:
        sizes: list[float] = []
        for page_number in range(doc.page_count):
            page = doc.load_page(page_number)
            for block in page.get_text("rawdict")["blocks"]:
                if block.get("type") != 0:
                    continue
                for line in block["lines"]:
                    for span in line["spans"]:
                        if span.get("chars"):
                            sizes.append(float(span.get("size", 10.0)))
        return sizes

    def _read_line(
        self, line: dict[str, Any], page_number: int
    ) -> tuple[str, list[PageRect | None], float]:
        chars: list[str] = []
        rects: list[PageRect | None] = []
        max_size = 0.0

        for span in line["spans"]:
            max_size = max(max_size, float(span.get("size", 0.0)))
            for char in span.get("chars", []):
                chars.append(char["c"])
                x0, y0, x1, y1 = char["bbox"]
                rects.append(PageRect(page=page_number, x0=x0, y0=y0, x1=x1, y1=y1))

        return "".join(chars), rects, max_size

    def _looks_like_heading(self, line: str, size: float, median: float) -> bool:
        stripped = line.strip()
        if not stripped or len(stripped) > 90:
            return False
        if size >= median * _HEADING_SIZE_RATIO:
            return True
        # ALL-CAPS short lines are the other common résumé heading convention
        # ("EXPERIENCE", "EDUCATION") and often carry no size change at all.
        return bool(stripped.isupper() and len(stripped.split()) <= 5)

    # ----------------------------------------------------------------- DOCX
    def _parse_docx(self, payload: bytes) -> ParsedDocument:
        from docx import Document as DocxDocument

        try:
            document = DocxDocument(io.BytesIO(payload))
        except Exception as exc:
            raise ValidationError("this DOCX could not be opened") from exc

        pieces: list[str] = []
        sections: list[tuple[int, str]] = []
        cursor = 0

        for paragraph in document.paragraphs:
            body = paragraph.text
            if not body.strip():
                continue
            if paragraph.style is not None and paragraph.style.name.startswith("Heading"):
                sections.append((cursor, body.strip()))
            pieces.append(body + "\n")
            cursor += len(body) + 1

        for table in document.tables:
            # Markdown-ish rows: a table flattened into prose retrieves terribly,
            # whereas row structure survives embedding reasonably well.
            for row in table.rows:
                rendered = " | ".join(cell.text.strip() for cell in row.cells) + "\n"
                pieces.append(rendered)
                cursor += len(rendered)

        text = "".join(pieces)
        return ParsedDocument(
            text=text,
            # DOCX has no fixed layout, so there is no geometry to report. Citations
            # degrade to text offsets and the viewer falls back to a text excerpt.
            char_rects=tuple([None] * len(text)),
            page_count=1,
            section_offsets=tuple(sections),
            warnings=("DOCX has no page geometry; citations will not draw highlights",),
        )

    # ----------------------------------------------------------------- text
    def _parse_text(self, body: str) -> ParsedDocument:
        sections = [
            (offset, line.lstrip("# ").strip())
            for offset, line in _line_offsets(body)
            if line.startswith("#")
        ]
        return ParsedDocument(
            text=body,
            char_rects=tuple([None] * len(body)),
            page_count=1,
            section_offsets=tuple(sections),
        )


def _line_offsets(body: str) -> list[tuple[int, str]]:
    offsets: list[tuple[int, str]] = []
    cursor = 0
    for line in body.splitlines(keepends=True):
        offsets.append((cursor, line.strip()))
        cursor += len(line)
    return offsets
