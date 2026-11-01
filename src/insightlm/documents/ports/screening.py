"""A second opinion on whether a sentence is trying to steer an automated judge."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol


class SentenceClassifier(Protocol):
    """Flags sentences that resemble attempts to steer a model.

    Optional and advisory: what it flags stays in the document and is shown to the
    reviewer. It exists because the offline scanner, measured on a held-out set, misses
    most reworded instructions (Finding 25).
    """

    @property
    def model_id(self) -> str: ...

    async def resembles_instruction(self, sentences: Sequence[str]) -> list[bool]: ...
