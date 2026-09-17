"""Where a piece of text physically sits in its source document.

This is the type the whole citation contract rests on. A claim cites a chunk; the
chunk carries a span; the span carries page geometry; the viewer draws a rectangle.
Character offsets alone cannot drive a highlight, which is why geometry is captured
at parse time -- it cannot be recovered later without re-parsing, and by then the
extracted text may have drifted.

The Week 1 spike (`spikes/pdf_geometry_spike.py`) proved the round-trip works,
including keeping two résumé columns distinct.
"""

from __future__ import annotations

from dataclasses import dataclass

from insightlm.shared.domain.errors import ValidationError


@dataclass(frozen=True, slots=True)
class PageRect:
    """One rectangle on one page, in PDF points with the origin top-left."""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self) -> None:
        if self.page < 0:
            raise ValidationError("page numbers are zero-based and non-negative")
        if self.x1 < self.x0 or self.y1 < self.y0:
            raise ValidationError("rectangle corners are out of order")

    @property
    def width(self) -> float:
        return self.x1 - self.x0

    @property
    def height(self) -> float:
        return self.y1 - self.y0

    def union(self, other: PageRect) -> PageRect:
        if other.page != self.page:
            raise ValidationError("cannot union rectangles on different pages")
        return PageRect(
            page=self.page,
            x0=min(self.x0, other.x0),
            y0=min(self.y0, other.y0),
            x1=max(self.x1, other.x1),
            y1=max(self.y1, other.y1),
        )


@dataclass(frozen=True, slots=True)
class TextSpan:
    """A half-open character range in a document's extracted text, plus its geometry.

    ``rects`` holds one rectangle per visual line, not one per character: a sentence
    wrapping across three lines highlights as three rectangles, which is what a reader
    expects to see.
    """

    char_start: int
    char_end: int
    rects: tuple[PageRect, ...]

    def __post_init__(self) -> None:
        if self.char_start < 0:
            raise ValidationError("char_start must be non-negative")
        if self.char_end <= self.char_start:
            raise ValidationError("span must cover at least one character")

    @property
    def length(self) -> int:
        return self.char_end - self.char_start

    @property
    def pages(self) -> tuple[int, ...]:
        return tuple(sorted({rect.page for rect in self.rects}))

    @property
    def first_page(self) -> int:
        """The page a citation should scroll to. Zero when geometry is unavailable."""
        return self.rects[0].page if self.rects else 0

    def contains(self, offset: int) -> bool:
        return self.char_start <= offset < self.char_end


def merge_rects_by_line(
    rects: list[PageRect], *, line_tolerance: float = 2.0
) -> tuple[PageRect, ...]:
    """Collapse per-character rectangles into one per visual line.

    Characters on the same line share a baseline within a point or two. Merging on
    that tolerance turns hundreds of glyph boxes into the handful of rectangles a
    highlight actually needs.
    """
    if not rects:
        return ()

    merged: list[PageRect] = []
    current = rects[0]

    for rect in rects[1:]:
        same_line = rect.page == current.page and abs(rect.y0 - current.y0) < line_tolerance
        if same_line:
            current = current.union(rect)
        else:
            merged.append(current)
            current = rect

    merged.append(current)
    return tuple(merged)
