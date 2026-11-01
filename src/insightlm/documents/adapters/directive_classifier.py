"""Nearest-exemplar classifier over sentence embeddings.

Each sentence is compared with a small set of labelled exemplars -- the dev set the
offline detector was tuned on, `data/directive-exemplars.json` -- and flagged when it
sits nearer the attacks than the honest sentences. The threshold was fitted on those
exemplars alone, by leave-one-out, and then measured once on the held-out set
(Finding 25). It is only valid for the embedding model it was fitted with, so the
classifier refuses any other.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path

from insightlm.shared.domain.errors import InvariantViolationError

EXEMPLARS = Path(__file__).parent / "data" / "directive-exemplars.json"
FITTED_MODEL = "gemini-embedding-001"
FITTED_THRESHOLD = 0.0149
"""From `insightlm-eval-injection --embedding`, 1024 dimensions. Refit if the model,
the dimensions or the exemplars change."""

_BATCH = 100  # the provider's limit per request
_NEIGHBOURS = 3

Embed = Callable[[list[str]], Awaitable[list[list[float]]]]


def knn_score(vector: list[float], attacks: list[list[float]], benign: list[list[float]]) -> float:
    """Mean similarity to the nearest attack exemplars minus the nearest honest ones.

    Vectors arrive unit-normed, so the dot product is the cosine.
    """

    def near(group: list[list[float]]) -> float:
        top = sorted(sum(x * y for x, y in zip(vector, g, strict=True)) for g in group)
        nearest = top[-_NEIGHBOURS:]
        return sum(nearest) / len(nearest)

    return near(attacks) - near(benign)


def load_exemplars() -> list[dict[str, str]]:
    items: list[dict[str, str]] = json.loads(EXEMPLARS.read_text())["items"]
    return items


async def embed_all(embed: Embed, texts: list[str]) -> list[list[float]]:
    return [
        vector
        for start in range(0, len(texts), _BATCH)
        for vector in await embed(texts[start : start + _BATCH])
    ]


class NearestExemplarClassifier:
    def __init__(self, embed: Embed, *, model_id: str, threshold: float = FITTED_THRESHOLD) -> None:
        if model_id != FITTED_MODEL:
            raise InvariantViolationError(
                f"the instruction classifier was fitted on {FITTED_MODEL}, not {model_id}"
            )
        self._embed = embed
        self._model_id = model_id
        self._threshold = threshold
        self._exemplars: tuple[list[list[float]], list[list[float]]] | None = None

    @property
    def model_id(self) -> str:
        return self._model_id

    async def _reference(self) -> tuple[list[list[float]], list[list[float]]]:
        # Embedded once per process, on first use: one request, then free.
        if self._exemplars is None:
            items = load_exemplars()
            vectors = await embed_all(self._embed, [i["text"] for i in items])
            attacks = [v for i, v in zip(items, vectors, strict=True) if i["label"] == "attack"]
            benign = [v for i, v in zip(items, vectors, strict=True) if i["label"] != "attack"]
            self._exemplars = (attacks, benign)
        return self._exemplars

    async def resembles_instruction(self, sentences: Sequence[str]) -> list[bool]:
        if not sentences:
            return []
        attacks, benign = await self._reference()
        vectors = await embed_all(self._embed, list(sentences))
        return [knn_score(v, attacks, benign) >= self._threshold for v in vectors]
