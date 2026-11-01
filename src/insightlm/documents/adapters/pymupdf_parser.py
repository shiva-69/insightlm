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
from insightlm.documents.domain.findings import (
    ENGINE_SCANNER,
    RGB,
    WHITE,
    ContentFinding,
    FindingKind,
    InstructionScanner,
    concealment,
)
from insightlm.documents.domain.spans import PageRect
from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.observability.logging import get_logger

_logger = get_logger(__name__)

_MIN_CHARS_PER_PAGE = 60
"""Below this many visible characters, a page carrying an image is treated as a scan
and read by OCR instead."""

_OCR_DPI = 300
"""Tesseract's accuracy falls off sharply below ~300 dpi on body-size type."""

_HEADING_SIZE_RATIO = 1.15
"""A span whose font is this much larger than the document's median is treated as a
heading. Ratio rather than an absolute size, because point sizes vary by template."""


class PyMuPdfParser:
    """Parses PDF natively; DOCX and text without geometry.

    ``exclude_concealed`` is the ingestion-time injection defence: text a reader can't
    see is kept out of the parsed text and reported as a finding instead. It is a
    switch only so the undefended baseline can be measured and demonstrated; nothing
    in production turns it off.

    A page with an image and no visible text is a scan, and is read by OCR. The rule is
    to trust the pixels, not the layers: an invisible text layer laid over a scan (which
    scanner software adds, and which an attacker can add too) is excluded like any other
    concealed text, and the page is read from what is actually drawn. OCR keeps
    per-character boxes, so citations on a scanned CV still highlight. ``ocr`` is off
    only where Tesseract is not installed; the document is then flagged, not dropped.
    """

    def __init__(
        self,
        *,
        exclude_concealed: bool = True,
        scanner: InstructionScanner = ENGINE_SCANNER,
        ocr: bool | None = None,
        ocr_language: str = "eng",
    ) -> None:
        self._exclude_concealed = exclude_concealed
        self._scanner = scanner
        self._ocr = tesseract_available() if ocr is None else ocr
        self._ocr_language = ocr_language

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
            findings: list[ContentFinding] = self._metadata_findings(doc)
            cursor = 0

            ocr_pages: list[int] = []
            unread_scans: list[int] = []

            for page_number in range(doc.page_count):
                page = doc.load_page(page_number)
                fills = self._filled_regions(page)
                findings.extend(self._off_page_findings(page, page_number))
                lines = self._read_page(page, page_number, fills, findings, median_size)

                if sum(len(t.strip()) for t, _, _ in lines) < _MIN_CHARS_PER_PAGE and (
                    page.get_images()
                ):
                    if self._ocr:
                        lines = self._ocr_page(page, page_number, findings)
                        ocr_pages.append(page_number)
                    else:
                        unread_scans.append(page_number)

                for line_text, line_rects, heading in lines:
                    if heading:
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

            if ocr_pages:
                warnings.append(
                    f"text on {_pages(ocr_pages)} was read by OCR from a scanned image; "
                    "check quoted wording against the highlighted page"
                )
                _logger.info("document_ocr", pages=len(ocr_pages), chars=len(text.strip()))
            if unread_scans:
                warnings.append(
                    f"{_pages(unread_scans)} looks scanned and OCR is not installed here, "
                    "so its text could not be read"
                )
                _logger.warning("document_scanned_without_ocr", pages=len(unread_scans))
            elif page_count and len(text.strip()) < _MIN_CHARS_PER_PAGE * page_count:
                warnings.append("very little extractable text in this document")
                _logger.warning("document_little_text", pages=page_count, chars=len(text.strip()))

            return ParsedDocument(
                text=text,
                char_rects=tuple(rects),
                page_count=page_count,
                section_offsets=tuple(sections),
                ocr_used=bool(ocr_pages),
                warnings=tuple(warnings),
                findings=tuple(findings),
            )
        finally:
            doc.close()

    def _read_page(
        self,
        page: pymupdf.Page,
        page_number: int,
        fills: list[tuple[pymupdf.Rect, RGB]],
        findings: list[ContentFinding],
        median_size: float,
        *,
        textpage: pymupdf.TextPage | None = None,
    ) -> list[tuple[str, list[PageRect | None], bool]]:
        """A page's non-empty lines in reading order, each flagged if it is a heading."""
        lines: list[tuple[str, list[PageRect | None], bool]] = []
        for block in page.get_text("rawdict", textpage=textpage)["blocks"]:
            if block.get("type") != 0:  # 0 = text; 1 = image
                continue
            for line in block["lines"]:
                line_text, line_rects, max_size = self._read_line(
                    line, page, page_number, fills, findings, recognised=textpage is not None
                )
                if line_text.strip():
                    heading = self._looks_like_heading(line_text, max_size, median_size)
                    lines.append((line_text, line_rects, heading))
        return lines

    def _ocr_page(
        self, page: pymupdf.Page, page_number: int, findings: list[ContentFinding]
    ) -> list[tuple[str, list[PageRect | None], bool]]:
        """Read a scanned page from its pixels. ``full`` ignores any existing text layer."""
        textpage = page.get_textpage_ocr(language=self._ocr_language, dpi=_OCR_DPI, full=True)
        # Recognised type sizes are estimates, so headings are judged against this page's
        # own median rather than the born-digital text elsewhere in the document.
        sizes = [
            float(span["size"])
            for block in page.get_text("dict", textpage=textpage)["blocks"]
            if block.get("type") == 0
            for line in block["lines"]
            for span in line["spans"]
            if span["text"].strip()
        ]
        median = statistics.median(sizes) if sizes else 10.0
        return self._read_page(page, page_number, [], findings, median, textpage=textpage)

    # ------------------------------------------------------- concealment
    @staticmethod
    def _rgb(value: int) -> RGB:
        return ((value >> 16 & 255) / 255, (value >> 8 & 255) / 255, (value & 255) / 255)

    @staticmethod
    def _filled_regions(page: pymupdf.Page) -> list[tuple[pymupdf.Rect, RGB]]:
        """Filled shapes, in paint order: what text actually sits on.

        Needed so white text on a dark banner reads as the design choice it is, and
        white text on a white box as the concealment it is. Without this, contrast is
        measured against an assumed white page and both answers come out wrong.
        """
        regions: list[tuple[pymupdf.Rect, RGB]] = []
        for drawing in page.get_drawings():
            fill = drawing.get("fill")
            if fill is not None and drawing.get("fill_opacity", 1.0) > 0.5:
                regions.append((pymupdf.Rect(drawing["rect"]), (fill[0], fill[1], fill[2])))
        return regions

    @staticmethod
    def _background(fills: list[tuple[pymupdf.Rect, RGB]], bbox: tuple[float, ...]) -> RGB:
        centre = pymupdf.Point((bbox[0] + bbox[2]) / 2, (bbox[1] + bbox[3]) / 2)
        background = WHITE
        for rect, colour in fills:  # later shapes paint over earlier ones
            if centre in rect:
                background = colour
        return background

    def _off_page_findings(self, page: pymupdf.Page, page_number: int) -> list[ContentFinding]:
        """Text placed outside the page. Extraction already clips it; this reports it."""
        unclipped = pymupdf.TEXTFLAGS_DICT & ~pymupdf.TEXT_MEDIABOX_CLIP
        width, height = page.rect.width, page.rect.height
        found: list[ContentFinding] = []
        # The flag alone isn't enough: extraction still clips to the page unless it is
        # given an explicit infinite clip. Found by the red-team corpus.
        blocks = page.get_text("dict", flags=unclipped, clip=pymupdf.INFINITE_RECT())["blocks"]
        for block in blocks:
            for line in block.get("lines", []):
                for span in line["spans"]:
                    x0, y0, x1, y1 = span["bbox"]
                    if x1 <= 0 or y1 <= 0 or x0 >= width or y0 >= height:
                        text = span["text"].strip()
                        if text:
                            found.append(
                                ContentFinding(
                                    kind=FindingKind.OFF_PAGE,
                                    text=text,
                                    page=page_number,
                                    excluded=True,
                                    instruction_like=self._scanner.matches(text),
                                )
                            )
        return found

    def _metadata_findings(self, doc: pymupdf.Document) -> list[ContentFinding]:
        found: list[ContentFinding] = []
        for field, value in (doc.metadata or {}).items():
            if isinstance(value, str) and self._scanner.matches(value):
                found.append(
                    ContentFinding(
                        kind=FindingKind.METADATA,
                        text=f"{field}: {value.strip()}",
                        excluded=True,
                        instruction_like=True,
                    )
                )
        return found

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
        self,
        line: dict[str, Any],
        page: pymupdf.Page,
        page_number: int,
        fills: list[tuple[pymupdf.Rect, RGB]],
        findings: list[ContentFinding],
        *,
        recognised: bool = False,
    ) -> tuple[str, list[PageRect | None], float]:
        chars: list[str] = []
        rects: list[PageRect | None] = []
        max_size = 0.0

        for span in line["spans"]:
            span_chars = span.get("chars", [])
            if not span_chars:
                continue
            x0, y0, x1, y1 = span["bbox"]
            span_rect = PageRect(page=page_number, x0=x0, y0=y0, x1=x1, y1=y1)
            # OCR output is an invisible layer by construction (alpha 0), but it was read
            # from what is drawn, so it is visible by definition.
            hidden = (
                None
                if recognised
                else concealment(
                    alpha=span.get("alpha", 255) / 255,
                    color=self._rgb(int(span.get("color", 0))),
                    background=self._background(fills, span["bbox"]),
                    size=float(span.get("size", 10.0)),
                    rect=span_rect,
                    page_width=page.rect.width,
                    page_height=page.rect.height,
                )
            )
            if hidden is not None:
                text = "".join(c["c"] for c in span_chars).strip()
                if text:
                    _append_merged(
                        findings,
                        ContentFinding(
                            kind=hidden,
                            text=text,
                            page=page_number,
                            rect=span_rect,
                            excluded=self._exclude_concealed,
                            instruction_like=self._scanner.matches(text),
                        ),
                        self._scanner,
                    )
                if self._exclude_concealed:
                    continue

            max_size = max(max_size, float(span.get("size", 0.0)))
            for char in span_chars:
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


def tesseract_available() -> bool:
    """Whether PyMuPDF can find Tesseract's language data on this machine."""
    try:
        return bool(pymupdf.get_tessdata())
    except RuntimeError:
        return False


def _pages(numbers: list[int]) -> str:
    """Zero-based page numbers as a reader would say them: "page 2", "pages 1-3, 5"."""
    ranges: list[str] = []
    start = prev = numbers[0]
    for n in [*numbers[1:], None]:
        if n is not None and n == prev + 1:
            prev = n
            continue
        ranges.append(f"{start + 1}" if start == prev else f"{start + 1}-{prev + 1}")
        if n is not None:
            start = prev = n
    return ("page " if len(numbers) == 1 else "pages ") + ", ".join(ranges)


def _line_offsets(body: str) -> list[tuple[int, str]]:
    offsets: list[tuple[int, str]] = []
    cursor = 0
    for line in body.splitlines(keepends=True):
        offsets.append((cursor, line.strip()))
        cursor += len(line)
    return offsets


def _append_merged(
    findings: list[ContentFinding], new: ContentFinding, scanner: InstructionScanner
) -> None:
    """One hidden sentence wrapped over three lines is one finding, not three.

    Merges into the previous finding when it is the same kind, on the same page, and
    starts within about a line and a half below it -- otherwise a reviewer is told
    "3 passages" for what is plainly one.
    """
    last = findings[-1] if findings else None
    if (
        last is not None
        and last.rect is not None
        and new.rect is not None
        and last.kind is new.kind
        and last.page == new.page
        and 0 <= new.rect.y0 - last.rect.y0 <= 1.5 * max(last.rect.y1 - last.rect.y0, 1.0)
    ):
        text = f"{last.text} {new.text}"
        findings[-1] = ContentFinding(
            kind=last.kind,
            text=text,
            page=last.page,
            rect=PageRect(
                page=last.rect.page,
                x0=min(last.rect.x0, new.rect.x0),
                y0=last.rect.y0,
                x1=max(last.rect.x1, new.rect.x1),
                y1=max(last.rect.y1, new.rect.y1),
            ),
            excluded=last.excluded,
            instruction_like=scanner.matches(text),
        )
        return
    findings.append(new)
