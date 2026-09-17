"""SPIKE: can a character range in extracted text be mapped back to a bounding box?

Everything downstream depends on a "yes". The citation contract promises that clicking
a claim highlights the exact span in the source PDF, which needs
`(char_start, char_end) -> [(page, x0, y0, x1, y1), ...]`. If this is not reliable, the
whole evidence story degrades to page-level citations and the design changes -- which is
why this is answered in Week 1 rather than discovered in Week 8.

Run:  uv run --with pymupdf python spikes/pdf_geometry_spike.py
"""

from __future__ import annotations

import sys
from dataclasses import dataclass

import pymupdf


@dataclass(frozen=True)
class CharBox:
    page: int
    char_index: int
    char: str
    bbox: tuple[float, float, float, float]


def extract_with_geometry(doc: pymupdf.Document) -> tuple[str, list[CharBox]]:
    """Extract page text and a per-character box, keeping the two index-aligned.

    Alignment is the whole trick: the text we chunk and embed must be the *same* string
    whose offsets we later resolve, so both are built in one pass.
    """
    text_parts: list[str] = []
    boxes: list[CharBox] = []
    cursor = 0

    for page_number, page in enumerate(doc):
        raw = page.get_text("rawdict")
        for block in raw["blocks"]:
            if block.get("type") != 0:  # 0 = text
                continue
            for line in block["lines"]:
                for span in line["spans"]:
                    for char in span["chars"]:
                        text_parts.append(char["c"])
                        boxes.append(
                            CharBox(
                                page=page_number,
                                char_index=cursor,
                                char=char["c"],
                                bbox=tuple(char["bbox"]),
                            )
                        )
                        cursor += 1
                text_parts.append("\n")
                boxes.append(
                    CharBox(page=page_number, char_index=cursor, char="\n", bbox=(0, 0, 0, 0))
                )
                cursor += 1

    return "".join(text_parts), boxes


def spans_for_range(
    boxes: list[CharBox], start: int, end: int
) -> list[tuple[int, tuple[float, float, float, float]]]:
    """Merge the character boxes in [start, end) into one rectangle per line."""
    selected = [b for b in boxes if start <= b.char_index < end and b.char.strip()]
    if not selected:
        return []

    merged: list[tuple[int, tuple[float, float, float, float]]] = []
    current_page = selected[0].page
    x0, y0, x1, y1 = selected[0].bbox

    for box in selected[1:]:
        same_line = box.page == current_page and abs(box.bbox[1] - y0) < 2.0
        if same_line:
            x0, y0 = min(x0, box.bbox[0]), min(y0, box.bbox[1])
            x1, y1 = max(x1, box.bbox[2]), max(y1, box.bbox[3])
        else:
            merged.append((current_page, (x0, y0, x1, y1)))
            current_page = box.page
            x0, y0, x1, y1 = box.bbox

    merged.append((current_page, (x0, y0, x1, y1)))
    return merged


def main() -> int:
    # A two-column layout with a table-ish run of lines: the shape a résumé actually has.
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Shivam P", fontsize=16)
    page.insert_text((72, 130), "Senior Engineer, Payments", fontsize=11)
    page.insert_text((72, 170), "Built an idempotent settlement pipeline", fontsize=10)
    page.insert_text((72, 190), "processing 10k TPS with exactly-once", fontsize=10)
    page.insert_text((320, 170), "Skills: Python, PostgreSQL,", fontsize=10)
    page.insert_text((320, 190), "Kubernetes, PCI-DSS", fontsize=10)

    text, boxes = extract_with_geometry(doc)

    print(f"extracted {len(text)} chars, {len(boxes)} character boxes")
    assert len(boxes) == len(text), "text and geometry are not index-aligned"

    needle = "idempotent settlement pipeline"
    start = text.index(needle)
    end = start + len(needle)
    print(f"\nlocating {needle!r} at chars [{start}:{end}]")
    print(f"  slice round-trips: {text[start:end] == needle}")

    spans = spans_for_range(boxes, start, end)
    for page_no, bbox in spans:
        print(
            f"  page {page_no}  bbox=({bbox[0]:.1f}, {bbox[1]:.1f}, {bbox[2]:.1f}, {bbox[3]:.1f})"
        )

    # Cross-column: a naive extractor interleaves the two columns and this text would
    # not exist as a contiguous run.
    skills = "Kubernetes, PCI-DSS"
    s2 = text.index(skills)
    right = spans_for_range(boxes, s2, s2 + len(skills))
    print(f"\nright column {skills!r}:")
    for page_no, bbox in right:
        print(
            f"  page {page_no}  bbox=({bbox[0]:.1f}, {bbox[1]:.1f}, {bbox[2]:.1f}, {bbox[3]:.1f})"
        )

    ok = bool(spans) and bool(right) and right[0][1][0] > spans[0][1][0]
    print(
        f"\nVERDICT: {'PASS' if ok else 'FAIL'} -- "
        f"{'char ranges resolve to boxes, columns stay distinct' if ok else 'geometry unreliable'}"
    )
    doc.close()
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
