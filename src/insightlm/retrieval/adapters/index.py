"""Writing chunks and their vectors into Postgres.

Chunks are persisted before their embeddings exist. That is deliberate: it makes the
embed step independently retryable, so a rate limit does not cost a re-parse.
"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from insightlm.documents.domain.spans import PageRect, TextSpan
from insightlm.retrieval.adapters.models import ChunkRow
from insightlm.retrieval.domain.chunks import Chunk
from insightlm.shared.domain.ids import ChunkId, DocumentVersionId, TenantId
from insightlm.shared.observability.logging import get_logger

_logger = get_logger(__name__)


def _to_chunk(row: ChunkRow) -> Chunk:
    rects = tuple(
        PageRect(
            page=int(item["page"]),
            x0=float(item["x0"]),
            y0=float(item["y0"]),
            x1=float(item["x1"]),
            y1=float(item["y1"]),
        )
        for item in row.rects
    )
    return Chunk(
        id=ChunkId(row.id),
        document_version_id=DocumentVersionId(row.document_version_id),
        tenant_id=TenantId(row.tenant_id),
        ordinal=row.ordinal,
        text=row.body,
        span=TextSpan(char_start=row.char_start, char_end=row.char_end, rects=rects),
        section_path=tuple(row.section_path),
        context_header=row.context_header,
    )


class PostgresChunkIndex:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def replace_chunks(self, version_id: DocumentVersionId, chunks: list[Chunk]) -> None:
        await self.delete_for_version(version_id)

        for chunk in chunks:
            self._session.add(
                ChunkRow(
                    id=chunk.id,
                    tenant_id=chunk.tenant_id,
                    document_version_id=chunk.document_version_id,
                    ordinal=chunk.ordinal,
                    body=chunk.text,
                    context_header=chunk.context_header,
                    section_path=list(chunk.section_path),
                    char_start=chunk.span.char_start,
                    char_end=chunk.span.char_end,
                    rects=[
                        {
                            "page": rect.page,
                            "x0": rect.x0,
                            "y0": rect.y0,
                            "x1": rect.x1,
                            "y1": rect.y1,
                        }
                        for rect in chunk.span.rects
                    ],
                    # `search_vector` is left unset on purpose: the database trigger
                    # computes it, so it can never drift from the body. `embedding`
                    # arrives in the next pipeline step.
                )
            )

        _logger.info("chunks_written", version_id=str(version_id), count=len(chunks))

    async def load_chunks(self, version_id: DocumentVersionId) -> list[Chunk]:
        rows = (
            await self._session.execute(
                select(ChunkRow)
                .where(ChunkRow.document_version_id == version_id)
                .order_by(ChunkRow.ordinal)
            )
        ).scalars()
        return [_to_chunk(row) for row in rows]

    async def attach_embeddings(
        self,
        version_id: DocumentVersionId,
        vectors: dict[ChunkId, list[float]],
        *,
        embedding_model: str,
    ) -> None:
        rows = (
            await self._session.execute(
                select(ChunkRow).where(ChunkRow.document_version_id == version_id)
            )
        ).scalars()

        attached = 0
        for row in rows:
            vector = vectors.get(ChunkId(row.id))
            if vector is None:
                continue
            row.embedding = vector
            row.embedding_model = embedding_model
            attached += 1

        _logger.info(
            "embeddings_attached",
            version_id=str(version_id),
            count=attached,
            embedding_model=embedding_model,
        )

    async def delete_for_version(self, version_id: DocumentVersionId) -> None:
        await self._session.execute(
            delete(ChunkRow).where(ChunkRow.document_version_id == version_id)
        )
