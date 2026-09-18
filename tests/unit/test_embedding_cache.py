"""The embedding cache.

The point is money: identical text must never be sent to a paid provider twice. The
tests count calls to the inner embedder, because "it returned the right vectors" is
necessary but not the property that matters.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from insightlm.retrieval.adapters.embedding import HashingEmbedder
from insightlm.retrieval.adapters.embedding_cache import CachingEmbedder, EmbeddingStore


class CountingEmbedder:
    """A stand-in for a paid provider that records every call it receives."""

    def __init__(self, model_id: str = "counting-v1") -> None:
        self._inner = HashingEmbedder(dimensions=16)
        self._model_id = model_id
        self.document_texts: list[str] = []
        self.query_texts: list[str] = []

    @property
    def dimensions(self) -> int:
        return 16

    @property
    def model_id(self) -> str:
        return self._model_id

    async def embed_documents(self, texts: list[str]) -> list[list[float]]:
        self.document_texts.extend(texts)
        return await self._inner.embed_documents(texts)

    async def embed_query(self, text: str) -> list[float]:
        self.query_texts.append(text)
        return await self._inner.embed_query(text)


@pytest.fixture
def store(tmp_path: Path) -> EmbeddingStore:
    return EmbeddingStore(tmp_path / "cache.sqlite")


async def test_a_second_request_for_the_same_text_costs_nothing(store: EmbeddingStore) -> None:
    provider = CountingEmbedder()
    cache = CachingEmbedder(provider, store)

    first = await cache.embed_documents(["alpha", "beta"])
    second = await cache.embed_documents(["alpha", "beta"])

    assert first == second
    assert provider.document_texts == ["alpha", "beta"]  # paid once, not twice
    assert cache.hits == 2
    assert cache.misses == 2


async def test_duplicates_inside_one_batch_are_paid_for_once(store: EmbeddingStore) -> None:
    provider = CountingEmbedder()
    cache = CachingEmbedder(provider, store)

    vectors = await cache.embed_documents(["same", "same", "same"])

    assert provider.document_texts == ["same"]
    assert vectors[0] == vectors[1] == vectors[2]


async def test_only_unseen_texts_reach_the_provider(store: EmbeddingStore) -> None:
    provider = CountingEmbedder()
    cache = CachingEmbedder(provider, store)

    await cache.embed_documents(["a", "b"])
    await cache.embed_documents(["a", "b", "c"])

    assert provider.document_texts == ["a", "b", "c"]


async def test_the_cache_survives_a_restart(tmp_path: Path) -> None:
    """Persistence is what saves money across evaluation runs, not just within one."""
    path = tmp_path / "cache.sqlite"

    first_provider = CountingEmbedder()
    first = CachingEmbedder(first_provider, EmbeddingStore(path))
    original = await first.embed_documents(["persisted"])

    second_provider = CountingEmbedder()
    second = CachingEmbedder(second_provider, EmbeddingStore(path))
    restored = await second.embed_documents(["persisted"])

    # float32 on disk, deliberately: pgvector stores float4 too, so the cache loses no
    # precision relative to the index it feeds, at half the space of float64.
    assert restored[0] == pytest.approx(original[0], rel=1e-6)
    assert second_provider.document_texts == []


async def test_a_different_model_is_a_miss_not_a_wrong_hit(store: EmbeddingStore) -> None:
    """Vectors from different models share nothing. A hit across models would put a
    wrong vector into the index with no error anywhere."""
    old = CachingEmbedder(CountingEmbedder("model-a"), store)
    await old.embed_documents(["text"])

    new_provider = CountingEmbedder("model-b")
    new = CachingEmbedder(new_provider, store)
    await new.embed_documents(["text"])

    assert new_provider.document_texts == ["text"]


async def test_queries_and_documents_are_cached_separately(store: EmbeddingStore) -> None:
    """Asymmetric models embed the same string differently depending on its role."""
    provider = CountingEmbedder()
    cache = CachingEmbedder(provider, store)

    await cache.embed_documents(["shared"])
    await cache.embed_query("shared")

    assert provider.document_texts == ["shared"]
    assert provider.query_texts == ["shared"]


async def test_the_cache_reports_the_inner_model_id(store: EmbeddingStore) -> None:
    """The index filters on model id; a cached vector is still that model's vector."""
    cache = CachingEmbedder(CountingEmbedder("gemini-embedding-001"), store)
    assert cache.model_id == "gemini-embedding-001"
