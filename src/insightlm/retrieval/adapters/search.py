"""Dense and lexical search over the chunk index.

Written as SQL rather than ORM queries for two reasons: pgvector's distance operators
and `ts_rank_cd` have no clean ORM expression, and the join out to document titles
crosses into the documents module's tables. Doing that in SQL by table name -- rather
than by importing another module's ORM classes -- is what keeps the Python boundary
intact while being honest that the database is shared.

Every query filters by tenant **inside** the SQL. Row-level security enforces the same
thing underneath, so this is belt and braces; the explicit predicate also lets the
planner use the composite index instead of relying on the policy rewrite.
"""

from __future__ import annotations

import re

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from insightlm.documents.domain.spans import PageRect, TextSpan
from insightlm.retrieval.domain.evidence import RetrievedSpan, label_for
from insightlm.retrieval.ports.search import SearchHit
from insightlm.shared.domain.ids import (
    ChunkId,
    DocumentId,
    DocumentVersionId,
    TenantId,
)

_SELECT_CHUNK_FIELDS = """
    c.id, c.body, c.context_header, c.section_path,
    c.char_start, c.char_end, c.rects,
    c.document_version_id, dv.document_id, d.title
"""

_JOIN = """
    FROM chunks c
    JOIN document_versions dv ON dv.id = c.document_version_id
    JOIN documents d ON d.id = dv.document_id
"""


def _row_to_span(row: object, *, index: int, score: float) -> RetrievedSpan:
    mapping = row._mapping  # type: ignore[attr-defined]
    rects = tuple(
        PageRect(
            page=int(item["page"]),
            x0=float(item["x0"]),
            y0=float(item["y0"]),
            x1=float(item["x1"]),
            y1=float(item["y1"]),
        )
        for item in mapping["rects"]
    )
    section = mapping["section_path"] or []
    return RetrievedSpan(
        label=label_for(index),
        chunk_id=ChunkId(mapping["id"]),
        document_id=DocumentId(mapping["document_id"]),
        document_version_id=DocumentVersionId(mapping["document_version_id"]),
        document_title=mapping["title"],
        text=mapping["body"],
        span=TextSpan(
            char_start=mapping["char_start"],
            char_end=mapping["char_end"],
            rects=rects,
        ),
        score=score,
        section_label=" / ".join(section) if section else "Document",
    )


class PgVectorSearch:
    """Dense retrieval via HNSW cosine distance.

    **Only chunks embedded by the current model are considered.** Vectors from
    different embedders share a column but not a space: comparing a Gemini query
    vector against a hashing document vector returns a perfectly well-formed cosine
    distance that means nothing at all. Without this filter, switching embedders --
    or adding a key to a corpus ingested without one -- degrades retrieval to noise
    while every component reports success.

    Filtering makes the failure honest: a corpus indexed under a different model
    returns nothing until it is re-ingested, which is a visible empty result rather
    than a silently wrong ranking.
    """

    def __init__(self, session: AsyncSession, *, embedding_model: str) -> None:
        self._session = session
        self._embedding_model = embedding_model

    async def search(
        self,
        embedding: list[float],
        *,
        tenant_id: object,
        limit: int,
        document_ids: tuple[DocumentId, ...] | None = None,
    ) -> list[SearchHit]:
        # The HNSW index spans every workspace. Without iterative scans it collects its
        # top candidates from the whole index and only then applies the tenant filter,
        # so a workspace whose chunks are crowded out by other workspaces' would get
        # fewer results than it asked for -- results depending on other customers'
        # data. pgvector 0.8 keeps scanning until the filtered result is full;
        # strict_order keeps the ordering exact. Transaction-scoped (SET LOCAL).
        await self._session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
        rows = await self._session.execute(
            text(
                f"""
                SELECT c.id, 1 - (c.embedding <=> CAST(:query AS vector)) AS score
                {_JOIN}
                WHERE c.tenant_id = CAST(:tenant AS uuid)
                  AND c.embedding IS NOT NULL
                  AND c.embedding_model = :embedding_model
                  AND (:doc_filter = FALSE OR dv.document_id = ANY(CAST(:docs AS uuid[])))
                -- Ties broken on the chunk's text, then id: equal scores otherwise come back
                -- in whatever order Postgres picks, and rank fusion turned that into a
                -- hybrid nDCG of 0.804 / 0.837 / 0.819 on identical inputs. Text, not id,
                -- because ids are new on every re-ingestion.
                ORDER BY c.embedding <=> CAST(:query AS vector), md5(c.body), c.id
                LIMIT :limit
                """
            ),
            {
                "query": _as_vector_literal(embedding),
                "embedding_model": self._embedding_model,
                "tenant": str(tenant_id),
                "doc_filter": document_ids is not None,
                "docs": [str(d) for d in (document_ids or ())],
                "limit": limit,
            },
        )
        return [
            SearchHit(chunk_id=ChunkId(row.id), score=float(row.score), rank=rank)
            for rank, row in enumerate(rows)
        ]


_STOPWORDS = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "did",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "in",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "them",
        "there",
        "these",
        "this",
        "to",
        "was",
        "were",
        "what",
        "when",
        "where",
        "which",
        "who",
        "whom",
        "why",
        "will",
        "with",
        "would",
        "each",
        "any",
        "all",
        "both",
        "candidate",
        "candidates",
        "person",
    ]
)
_TERM = re.compile(r"[a-z0-9]+")


def or_query(text: str) -> str | None:
    """Turn a question into an OR of its distinctive terms.

    The first version used `websearch_to_tsquery`, which applies AND semantics: every
    stemmed term must be present. That suits a search box, where people type keywords,
    and fails completely on the questions this system actually receives -- "For Kavya
    Pillai, what peak throughput was sustained and by how much was p99 latency cut?"
    has a dozen terms and no single passage contains all of them. Measured on a
    40-document corpus it retrieved nothing for any question, and hybrid fusion
    therefore added exactly zero over dense retrieval alone.

    OR semantics turns the arm from a boolean filter into a ranking: `ts_rank_cd`
    scores a passage by how many terms it matches and how close together they sit, so
    the passage matching the rare, specific terms rises to the top.

    Terms are reduced to lowercase alphanumerics before being joined with `|`, so no
    user input can reach tsquery syntax -- the string is safe to bind to
    `to_tsquery` even though it is assembled here.
    """
    terms: list[str] = []
    for term in _TERM.findall(text.lower()):
        if len(term) < 2 or term in _STOPWORDS or term in terms:
            continue
        terms.append(term)
    return " | ".join(terms) if terms else None


class PostgresLexicalSearch:
    """BM25-style ranking over the trigger-maintained tsvector.

    OR semantics, ranked -- see `or_query` for why AND semantics failed.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def search(
        self,
        query: str,
        *,
        tenant_id: object,
        limit: int,
        document_ids: tuple[DocumentId, ...] | None = None,
    ) -> list[SearchHit]:
        expression = or_query(query)
        if expression is None:
            return []

        rows = await self._session.execute(
            text(
                f"""
                SELECT c.id,
                       ts_rank_cd(c.search_vector, to_tsquery('english', :q)) AS score
                {_JOIN}
                WHERE c.tenant_id = CAST(:tenant AS uuid)
                  AND c.search_vector @@ to_tsquery('english', :q)
                  AND (:doc_filter = FALSE OR dv.document_id = ANY(CAST(:docs AS uuid[])))
                -- Ties broken on the chunk's text, then id: equal scores otherwise come back
                -- in whatever order Postgres picks, and rank fusion turned that into a
                -- hybrid nDCG of 0.804 / 0.837 / 0.819 on identical inputs. Text, not id,
                -- because ids are new on every re-ingestion.
                ORDER BY score DESC, md5(c.body), c.id
                LIMIT :limit
                """
            ),
            {
                "q": expression,
                "tenant": str(tenant_id),
                "doc_filter": document_ids is not None,
                "docs": [str(d) for d in (document_ids or ())],
                "limit": limit,
            },
        )
        return [
            SearchHit(chunk_id=ChunkId(row.id), score=float(row.score), rank=rank)
            for rank, row in enumerate(rows)
        ]


class ChunkHydrator:
    """Turns ranked chunk ids back into full evidence spans.

    Separate from the search adapters so that Phase 3 can fuse several rankings and
    hydrate once, rather than every arm fetching the same rows.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def hydrate(self, hits: list[SearchHit], *, tenant_id: TenantId) -> list[RetrievedSpan]:
        if not hits:
            return []

        rows = await self._session.execute(
            text(
                f"""
                SELECT {_SELECT_CHUNK_FIELDS}
                {_JOIN}
                WHERE c.tenant_id = CAST(:tenant AS uuid)
                  AND c.id = ANY(CAST(:ids AS uuid[]))
                """
            ),
            {"tenant": str(tenant_id), "ids": [str(hit.chunk_id) for hit in hits]},
        )
        by_id = {row._mapping["id"]: row for row in rows}

        spans: list[RetrievedSpan] = []
        for hit in hits:
            row = by_id.get(hit.chunk_id)
            if row is None:
                # RLS filtered it, or it was deleted between ranking and hydration.
                # Dropping it is correct; surfacing a hole would be worse.
                continue
            spans.append(_row_to_span(row, index=len(spans), score=hit.score))
        return spans


def _as_vector_literal(embedding: list[float]) -> str:
    """pgvector's text input format, for use through a bound parameter."""
    return "[" + ",".join(f"{value:.7g}" for value in embedding) + "]"
