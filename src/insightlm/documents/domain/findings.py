"""What a document hides, and what it tries to say to a model.

A CV is written by the person it will be judged on, so it's untrusted input to every
model that reads it. The attack that matters is cheap: white-on-white text saying
"this candidate is an expert in everything", or a line addressed to the model instead
of the reader. A person reviewing the PDF never sees it; a pipeline that indexes raw
text feeds it straight to the judge.

Two different responses, on purpose:

- **Concealed text** (invisible, low-contrast against its actual background, too small
  to read, or off the page) is *excluded* from what gets indexed, and recorded. Nobody
  can read it, so nothing that judges the candidate should either.
- **Instruction-like text** that is visible stays in. It's what the document says, and
  a reviewer can see it. It's *flagged*, and the prompts treat every source as data.

Every rule here is a threshold on a measurable property, and the red-team corpus
measures both how many attacks each rule catches and how many honest CVs it wrongly
flags. A detector with an unmeasured false-positive rate is a new way to be unfair.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from enum import StrEnum

from insightlm.documents.domain.directives import DirectiveDetector
from insightlm.documents.domain.spans import PageRect
from insightlm.shared.domain.errors import InvariantViolationError

RGB = tuple[float, float, float]
WHITE: RGB = (1.0, 1.0, 1.0)

# Below this opacity, text is not meant to be seen (render mode 3 and fill opacity 0
# both arrive as alpha 0).
MIN_VISIBLE_ALPHA = 0.1
# WCAG contrast ratio. Body text should be >= 4.5; light-grey design text sits around
# 2.5-3; a faint watermark around 1.25 is pale but perceptible. Near-white-on-white
# (the attack) sits near 1.05. The line is drawn at what a reader can perceive, not at
# what is comfortable to read: faint is a design choice, invisible is concealment.
MIN_VISIBLE_CONTRAST = 1.2
# Fine print on real CVs goes down to about 6pt. Below 3pt nobody reads it.
MIN_READABLE_SIZE = 3.0


class FindingKind(StrEnum):
    INVISIBLE = "invisible_text"
    LOW_CONTRAST = "low_contrast_text"
    MICRO = "micro_text"
    OFF_PAGE = "off_page_text"
    METADATA = "metadata_instruction"
    INSTRUCTION = "instruction_like_text"
    RESEMBLES_INSTRUCTION = "resembles_instruction_text"
    """Not matched by any rule, but close to known attempts to steer a model."""


CONCEALMENT = frozenset(
    {FindingKind.INVISIBLE, FindingKind.LOW_CONTRAST, FindingKind.MICRO, FindingKind.OFF_PAGE}
)


@dataclass(frozen=True, slots=True)
class ContentFinding:
    kind: FindingKind
    text: str
    page: int | None = None
    rect: PageRect | None = None
    # Whether the text was kept out of the index. True for concealed text.
    excluded: bool = False
    # Whether the text also reads as an instruction to a model.
    instruction_like: bool = False


def _luminance(rgb: RGB) -> float:
    def channel(c: float) -> float:
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = (channel(c) for c in rgb)
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def contrast_ratio(foreground: RGB, background: RGB) -> float:
    """WCAG 2 contrast ratio, 1 (identical) to 21 (black on white)."""
    lighter, darker = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


def concealment(
    *,
    alpha: float,
    color: RGB,
    background: RGB,
    size: float,
    rect: PageRect,
    page_width: float,
    page_height: float,
) -> FindingKind | None:
    """Why a span can't be read by a person, or None if it can."""
    if alpha < MIN_VISIBLE_ALPHA:
        return FindingKind.INVISIBLE
    if rect.x1 <= 0 or rect.y1 <= 0 or rect.x0 >= page_width or rect.y0 >= page_height:
        return FindingKind.OFF_PAGE
    if size < MIN_READABLE_SIZE:
        return FindingKind.MICRO
    if contrast_ratio(color, background) < MIN_VISIBLE_CONTRAST:
        return FindingKind.LOW_CONTRAST
    return None


# Phrases addressed to a model rather than a reader. Deliberately specific: "rate" alone
# would flag "rate limiting", "ignore" alone would flag "ignored flaky tests". Each
# pattern needs the imperative *and* its object.
#
# These are the engine's: they are about addressing a model, whatever the document is.
# A vertical adds the phrases specific to what it judges -- recruiting adds "rate this
# candidate", a contract vertical would add "approve this clause" -- through
# ``InstructionScanner.extended``, in the composition root.
ENGINE_INSTRUCTION_PATTERNS: tuple[str, ...] = (
    r"\bignore\s+(?:all\s+|any\s+|the\s+)?(?:previous|prior|above|earlier|preceding)\s+"
    r"(?:instructions?|prompts?|directions?|rules?|context)",
    r"\b(?:disregard|forget|override)\s+(?:all\s+|any\s+)?(?:previous|prior|above|your|the)\s+"
    r"(?:instructions?|rules?|guidelines?|criteria|prompts?)",
    r"\byou\s+are\s+(?:now\s+)?(?:an?\s+|the\s+)?(?:ai|assistant|language\s+model|llm|"
    r"chatbot|evaluator|screener|grader)\b",
    r"\b(?:system|developer)\s+(?:prompt|message|instructions?)\b",
    r"\bas\s+an?\s+(?:ai|language\s+model|llm)\b",
    r"\bnew\s+instructions?\s*:",
    r"</?\s*(?:source|system|instructions?|assistant|user)\s*>",
    r"\b(?:verdict|output|respond|answer)\s*(?:with|:)\s*[\"']?strong\b",
)


@dataclass(frozen=True, slots=True)
class InstructionScanner:
    """Finds visible text addressed to a model. Pure; built once, shared freely.

    Two layers: ``patterns`` for stock phrasing, and ``directives`` for sentences
    reworded to avoid it (see `directives`). Either one flags a passage.
    """

    patterns: tuple[str, ...]
    directives: DirectiveDetector = field(default_factory=DirectiveDetector)
    _compiled: re.Pattern[str] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        if not self.patterns:
            raise InvariantViolationError("an instruction scanner needs at least one pattern")
        compiled = re.compile("|".join(f"(?:{p})" for p in self.patterns), re.IGNORECASE)
        object.__setattr__(self, "_compiled", compiled)

    def extended(
        self, patterns: Sequence[str], *, subjects: Sequence[str] = ()
    ) -> InstructionScanner:
        """This scanner plus a vertical's own phrases, and the words for what it judges."""
        return InstructionScanner((*self.patterns, *patterns), self.directives.extended(subjects))

    def matches(self, text: str) -> bool:
        return bool(self._compiled.search(" ".join(text.split()))) or bool(
            self.directives.spans(text)
        )

    def spans(self, text: str) -> list[tuple[int, int]]:
        """Character ranges of instruction-like phrases, widened to their sentence."""
        found: list[tuple[int, int]] = []
        for match in self._compiled.finditer(text):
            start = max(text.rfind(c, 0, match.start()) for c in ".!?\n") + 1
            ends = [i for c in ".!?\n" if (i := text.find(c, match.end())) != -1]
            found.append((start, min(ends) + 1 if ends else len(text)))
        found.extend(self.directives.spans(text))

        ranges: list[tuple[int, int]] = []
        for start, end in sorted(found):
            if ranges and start < ranges[-1][1]:
                ranges[-1] = (ranges[-1][0], max(end, ranges[-1][1]))
            else:
                ranges.append((start, end))
        return ranges

    def findings(
        self, text: str, char_rects: Sequence[PageRect | None] = ()
    ) -> tuple[ContentFinding, ...]:
        """Visible text addressed to a model: kept in the document, flagged for the reviewer."""
        found: list[ContentFinding] = []
        for start, end in self.spans(text):
            rect = next((r for r in char_rects[start:end] if r is not None), None)
            found.append(
                ContentFinding(
                    kind=FindingKind.INSTRUCTION,
                    text=" ".join(text[start:end].split()),
                    page=rect.page if rect else None,
                    rect=rect,
                    excluded=False,
                    instruction_like=True,
                )
            )
        return tuple(found)


ENGINE_SCANNER = InstructionScanner(ENGINE_INSTRUCTION_PATTERNS)
"""The engine alone, with no vertical's phrases. Used where no vertical is wired in."""
