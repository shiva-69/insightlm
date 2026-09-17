"""Embedding adapters.

Two of them, for a reason worth stating plainly.

`HashingEmbedder` is deterministic, dependency-free and offline. It makes the entire
ingestion and retrieval path testable in CI with no API key, no network and no
multi-gigabyte torch download -- and because it is deterministic, retrieval tests
assert on exact rankings instead of tolerances. What it is **not** is semantic: it
matches on shared character n-grams, so it behaves like a fuzzy lexical matcher and
will not connect "built the checkout flow" to "payment integration experience". That
is a real quality ceiling, and it is the honest default rather than a hidden one.

`VoyageEmbedder` is the real one. Voyage is Anthropic's recommended embedding partner
(the Claude API has no embeddings endpoint of its own).

Which is in use is recorded on every indexed chunk and every answer, so a result is
always attributable to the embedder that produced it -- and Phase 2's eval harness
measures the gap between them rather than assuming it.
"""

from __future__ import annotations

import hashlib
import math
import re
from itertools import pairwise

import httpx
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential_jitter

from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.observability.logging import get_logger

_logger = get_logger(__name__)

EMBEDDING_DIMENSIONS = 1024
"""Fixed for the whole index.

pgvector's HNSW index requires a fixed column dimension, so changing this is a
migration plus a full reindex -- not a config tweak. 1024 matches voyage-3, which is
what makes the swap from the offline embedder to the real one a config change and not
a schema change.
"""

_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEmbedder:
    """Offline, deterministic, no dependencies.

    Word unigrams and bigrams plus character 4-grams are hashed into a fixed vector
    with signed buckets, then L2-normalised so cosine distance behaves. Character
    n-grams give it some robustness to morphology ("settle"/"settlement") that pure
    word hashing lacks.
    """

    def __init__(self, *, dimensions: int = EMBEDDING_DIMENSIONS) -> None:
        self._dimensions = dimensions

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_id(self) -> str:
        return f"hashing-{self._dimensions}-v1"

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(text) for text in texts]

    async def embed_query(self, text: str) -> list[float]:
        return self._embed(text)

    def _embed(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        words = _TOKEN.findall(text.lower())

        for feature, weight in self._features(words, text.lower()):
            digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dimensions
            # The sign bit spreads collisions in both directions instead of letting
            # them all accumulate positively.
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign * weight

        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]

    def _features(self, words: list[str], lowered: str) -> list[tuple[str, float]]:
        features: list[tuple[str, float]] = [(word, 1.0) for word in words]
        features += [(f"{a}_{b}", 1.5) for a, b in pairwise(words)]
        squashed = re.sub(r"\s+", " ", lowered)
        features += [(squashed[i : i + 4], 0.35) for i in range(0, max(0, len(squashed) - 3), 2)]
        return features


class VoyageEmbedder:
    """Voyage AI embeddings.

    Asymmetric: documents and queries are embedded with different ``input_type``
    hints. Using the wrong side costs recall quietly, which is exactly why the port
    separates the two methods rather than offering one ``embed()``.
    """

    _ENDPOINT = "https://api.voyageai.com/v1/embeddings"

    def __init__(
        self,
        api_key: str,
        *,
        model: str = "voyage-3",
        dimensions: int = EMBEDDING_DIMENSIONS,
        timeout: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValidationError("VoyageEmbedder needs an API key")
        self._api_key = api_key
        self._model = model
        self._dimensions = dimensions
        self._timeout = timeout

    @property
    def dimensions(self) -> int:
        return self._dimensions

    @property
    def model_id(self) -> str:
        return self._model

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        return await self._call(texts, input_type="document")

    async def embed_query(self, text: str) -> list[float]:
        vectors = await self._call([text], input_type="query")
        return vectors[0]

    @retry(
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential_jitter(initial=0.5, max=8.0),
        reraise=True,
    )
    async def _call(self, texts: list[str], *, input_type: str) -> list[list[float]]:
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
                self._ENDPOINT,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json={
                    "input": texts,
                    "model": self._model,
                    "input_type": input_type,
                    "output_dimension": self._dimensions,
                },
            )
            response.raise_for_status()
            payload = response.json()

        # Order is not guaranteed by the response shape; sort by the echoed index
        # rather than trusting position, or chunk N gets chunk M's vector.
        ordered = sorted(payload["data"], key=lambda item: item["index"])
        return [item["embedding"] for item in ordered]
