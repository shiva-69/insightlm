"""The ingestion pipeline.

**Why this lives in the worker.** A pipeline step reads a document's parsed text
(`documents`) and writes chunks (`retrieval`), so it spans two modules. Neither module
may compose the other -- `import-linter` forbids reaching into another module's
adapters, and that rule is what keeps them independently replaceable. Composing them
is the delivery layer's job, which is exactly what this module is.

**Resumability is real, not decorative.** Each step persists its own output and is
recorded on the job before the next one starts. A failure at `embed` -- a rate limit,
a provider outage -- resumes at `embed`, not at `parse`. For a 300-page PDF that is
the difference between a retry costing a second and costing a minute.

**Failure is designed for.** Retries are bounded. When they are exhausted the work
lands in `dead_letters`, visible to an operator, rather than vanishing. A résumé that
silently failed to parse is a candidate who silently disappeared from the pipeline,
which is precisely the class of failure this project exists to make impossible.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.documents.adapters.models import DeadLetterRow
from insightlm.documents.adapters.uow import SqlAlchemyDocumentsUnitOfWork
from insightlm.documents.domain.directives import sentence_spans
from insightlm.documents.domain.entities import (
    ExtractedText,
    IngestionJob,
    IngestionStep,
    MediaType,
    ParsedDocument,
)
from insightlm.documents.domain.findings import (
    ENGINE_SCANNER,
    ContentFinding,
    FindingKind,
    InstructionScanner,
)
from insightlm.documents.ports.parsing import DocumentParser
from insightlm.documents.ports.screening import SentenceClassifier
from insightlm.documents.ports.storage import ObjectStore
from insightlm.retrieval.adapters.index import PostgresChunkIndex
from insightlm.retrieval.ports.indexing import Chunker, Embedder
from insightlm.shared.domain.errors import DomainError, NotFoundError, ValidationError
from insightlm.shared.domain.ids import DocumentVersionId, TenantId
from insightlm.shared.observability.logging import get_logger
from insightlm.shared.observability.telemetry import DEAD_LETTERS, INGESTION_STEP, tracer
from insightlm.shared.ports.clock import Clock

_logger = get_logger(__name__)

MAX_ATTEMPTS = 3
RETRY_BASE_SECONDS = 5.0
EMBED_BATCH_SIZE = 64


class IngestionUnitOfWork(SqlAlchemyDocumentsUnitOfWork):
    """Documents' unit of work plus the chunk index.

    Composed here rather than in either module, so one transaction can advance the
    job and write the chunks together -- the property that makes a recorded step
    actually mean the work behind it is durable.
    """

    def _build_repositories(self, session: AsyncSession) -> None:
        super()._build_repositories(session)
        self.chunks = PostgresChunkIndex(session)


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    version_id: DocumentVersionId
    completed: bool
    steps_run: tuple[IngestionStep, ...]
    dead_lettered: bool = False
    # Seconds until the next attempt, when one should happen. None when finished or
    # dead-lettered. The worker schedules it; nothing else retries a failed step.
    retry_in: float | None = None
    error: str | None = None


class IngestionPipeline:
    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        store: ObjectStore,
        parser: DocumentParser,
        chunker: Chunker,
        embedder: Embedder,
        clock: Clock,
        scanner: InstructionScanner = ENGINE_SCANNER,
        classifier: SentenceClassifier | None = None,
    ) -> None:
        self._scanner = scanner
        self._classifier = classifier
        self._session_factory = session_factory
        self._store = store
        self._parser = parser
        self._chunker = chunker
        self._embedder = embedder
        self._clock = clock

    def _uow(self, tenant_id: TenantId) -> IngestionUnitOfWork:
        return IngestionUnitOfWork(self._session_factory, self._clock, tenant_id=tenant_id)

    async def run(self, *, tenant_id: TenantId, version_id: DocumentVersionId) -> IngestionOutcome:
        steps_run: list[IngestionStep] = []

        while True:
            async with self._uow(tenant_id) as uow:
                job = await uow.jobs.for_version(version_id)
                if job is None:
                    raise NotFoundError(
                        "no ingestion job for this version", detail={"id": str(version_id)}
                    )
                step = job.next_step

            if step is None:
                await self._finalise(tenant_id, version_id)
                return IngestionOutcome(
                    version_id=version_id, completed=True, steps_run=tuple(steps_run)
                )

            try:
                await self._run_step(tenant_id, version_id, step)
                steps_run.append(step)
            except Exception as exc:
                return await self._handle_failure(
                    tenant_id, version_id, step, exc, tuple(steps_run)
                )

    async def _run_step(
        self, tenant_id: TenantId, version_id: DocumentVersionId, step: IngestionStep
    ) -> None:
        _logger.info("ingestion_step_started", step=step.value, version_id=str(version_id))
        started = time.perf_counter()

        handlers = {
            IngestionStep.PARSE: self._step_parse,
            IngestionStep.SANITISE: self._step_sanitise,
            IngestionStep.CHUNK: self._step_chunk,
            IngestionStep.EMBED: self._step_embed,
            IngestionStep.INDEX: self._step_index,
        }
        with tracer.start_as_current_span(f"ingestion.{step.value}"):
            await handlers[step](tenant_id, version_id)
        INGESTION_STEP.record((time.perf_counter() - started) * 1000, {"step": step.value})

        # Recorded only after the work committed, so a crash between the two costs a
        # redo (every step is idempotent) rather than a silent skip.
        async with self._uow(tenant_id) as uow:
            job = await uow.jobs.for_version(version_id)
            if job is not None:
                await uow.jobs.update(job.step_completed(step))
            await uow.commit()

    # ------------------------------------------------------------------ steps
    async def _step_parse(self, tenant_id: TenantId, version_id: DocumentVersionId) -> None:
        async with self._uow(tenant_id) as uow:
            version = await uow.versions.get(version_id)
            if version is None:
                raise NotFoundError("document version not found")
            document = await uow.documents.get(version.document_id)
            if document is None:
                raise NotFoundError("document not found")
            await uow.versions.update(version.processing())
            await uow.commit()
            media_type = document.media_type
            storage_key = version.storage_key

        payload = await self._store.get(storage_key)
        parsed = await self._parser.parse(payload, media_type=MediaType(media_type))

        # Only the text is persisted. Per-character geometry would be tens of MB for a
        # long PDF; the merged rectangles that a highlight actually needs are written
        # onto each chunk instead. See `ExtractedText`.
        # Visible text addressed to a model is kept -- it's what the document says, and
        # a reviewer can read it -- but recorded, so the reviewer is told to.
        instructions = self._scanner.findings(parsed.text, parsed.char_rects)
        instructions += await self._resembling(parsed)
        async with self._uow(tenant_id) as uow:
            await uow.versions.save_text(
                ExtractedText(
                    document_version_id=version_id,
                    text=parsed.text,
                    page_count=parsed.page_count,
                    warnings=parsed.warnings,
                    findings=parsed.findings + instructions,
                    ocr_used=parsed.ocr_used,
                )
            )
            await uow.commit()

        if parsed.warnings:
            _logger.warning(
                "document_parsed_with_warnings",
                version_id=str(version_id),
                warnings=list(parsed.warnings),
            )

    async def _resembling(self, parsed: ParsedDocument) -> tuple[ContentFinding, ...]:
        """Sentences no rule matched that the classifier says resemble an attempt to steer.

        Advisory, so a provider failure costs the second opinion, never the document.
        """
        if self._classifier is None:
            return ()
        text = parsed.text
        covered = self._scanner.spans(text)
        candidates = [
            (a, b)
            for a, b in sentence_spans(text)
            if not any(a < end and start < b for start, end in covered)
        ]
        try:
            verdicts = await self._classifier.resembles_instruction(
                [text[a:b] for a, b in candidates]
            )
        except Exception as exc:
            _logger.warning("instruction_classifier_unavailable", error=type(exc).__name__)
            return ()
        found: list[ContentFinding] = []
        for (a, b), flagged in zip(candidates, verdicts, strict=True):
            if not flagged:
                continue
            rect = next((r for r in parsed.char_rects[a:b] if r is not None), None)
            found.append(
                ContentFinding(
                    kind=FindingKind.RESEMBLES_INSTRUCTION,
                    text=" ".join(text[a:b].split()),
                    page=rect.page if rect else None,
                    rect=rect,
                    excluded=False,
                    instruction_like=True,
                )
            )
        return tuple(found)

    async def _step_sanitise(self, tenant_id: TenantId, version_id: DocumentVersionId) -> None:
        """Placeholder for the Phase 6 defences, wired now so the shape is fixed.

        Phase 6 adds hidden-text detection, PII spans and the prompt-injection scan.
        Phase 1 asserts only that there is text to work with -- an empty parse is a
        failure worth surfacing, not a document that silently answers nothing.
        """
        async with self._uow(tenant_id) as uow:
            extracted = await uow.versions.load_text(version_id)
            if extracted is None or not extracted.text.strip():
                raise ValidationError(
                    "no text could be extracted -- the document is empty, scanned, or unreadable"
                )

    async def _step_chunk(self, tenant_id: TenantId, version_id: DocumentVersionId) -> None:
        # Re-parses from the stored bytes rather than the stored text, because
        # chunking needs the geometry that `ExtractedText` deliberately does not keep.
        async with self._uow(tenant_id) as uow:
            version = await uow.versions.get(version_id)
            document = await uow.documents.get(version.document_id) if version else None
            if version is None or document is None:
                raise NotFoundError("document version not found")
            storage_key = version.storage_key
            media_type = document.media_type
            title = document.title

        payload = await self._store.get(storage_key)
        parsed = await self._parser.parse(payload, media_type=MediaType(media_type))
        # The title is passed so each chunk carries its document's identity into the
        # embedding. Without it a passage about an achievement says nothing about
        # whose it is, and a question naming a person cannot reach it.
        chunks = self._chunker.chunk(
            parsed,
            tenant_id=tenant_id,
            document_version_id=version_id,
            document_title=title,
        )
        if not chunks:
            raise ValueError("chunking produced nothing retrievable")

        async with self._uow(tenant_id) as uow:
            await uow.chunks.replace_chunks(version_id, chunks)
            await uow.commit()

    async def _step_embed(self, tenant_id: TenantId, version_id: DocumentVersionId) -> None:
        async with self._uow(tenant_id) as uow:
            chunks = await uow.chunks.load_chunks(version_id)
        if not chunks:
            raise ValueError("no chunks to embed")

        vectors: dict[object, list[float]] = {}
        for start in range(0, len(chunks), EMBED_BATCH_SIZE):
            batch = chunks[start : start + EMBED_BATCH_SIZE]
            embeddings = await self._embedder.embed_documents(
                [chunk.embedding_text for chunk in batch]
            )
            vectors.update(
                {chunk.id: vector for chunk, vector in zip(batch, embeddings, strict=True)}
            )

        async with self._uow(tenant_id) as uow:
            await uow.chunks.attach_embeddings(
                version_id,
                vectors,  # type: ignore[arg-type]
                embedding_model=self._embedder.model_id,
            )
            await uow.commit()

    async def _step_index(self, tenant_id: TenantId, version_id: DocumentVersionId) -> None:
        """Nothing to build.

        The HNSW and GIN indexes are maintained by Postgres, and `search_vector` by a
        trigger. The step exists so the pipeline has an explicit completion point and
        so a future reranker or sparse index has somewhere to go.
        """
        async with self._uow(tenant_id) as uow:
            chunks = await uow.chunks.load_chunks(version_id)
        if not chunks:
            raise ValueError("indexing found no chunks")

    async def _finalise(self, tenant_id: TenantId, version_id: DocumentVersionId) -> None:
        async with self._uow(tenant_id) as uow:
            version = await uow.versions.get(version_id)
            extracted = await uow.versions.load_text(version_id)
            job = await uow.jobs.for_version(version_id)
            if version is None or extracted is None or job is None:
                raise NotFoundError("document version not found")

            await uow.versions.update(
                version.ready(page_count=extracted.page_count, char_count=len(extracted.text))
            )
            await uow.jobs.update(job.succeeded())
            await uow.commit()

        _logger.info("ingestion_completed", version_id=str(version_id))

    # ---------------------------------------------------------------- failure
    async def _handle_failure(
        self,
        tenant_id: TenantId,
        version_id: DocumentVersionId,
        step: IngestionStep,
        exc: Exception,
        steps_run: tuple[IngestionStep, ...],
    ) -> IngestionOutcome:
        message = f"{type(exc).__name__}: {exc}"
        _logger.error(
            "ingestion_step_failed",
            step=step.value,
            version_id=str(version_id),
            error=message,
        )

        async with self._uow(tenant_id) as uow:
            job = await uow.jobs.for_version(version_id)
            if job is None:
                raise NotFoundError("ingestion job not found") from exc

            failed: IngestionJob = job.failed(message)
            # completed_steps survives, which is what makes the retry resume rather
            # than restart.
            await uow.jobs.update(failed)

            # A file that can't be opened, or holds no text, fails the same way every
            # time: retrying only delays telling the user. Anything else may be
            # transient (storage, database, embedding provider) and gets retried.
            permanent = isinstance(exc, DomainError)
            exhausted = permanent or failed.attempts >= MAX_ATTEMPTS
            if exhausted:
                version = await uow.versions.get(version_id)
                if version is not None:
                    await uow.versions.update(version.failed(message))
                uow.session.add(
                    DeadLetterRow(
                        id=uuid4(),
                        tenant_id=tenant_id,
                        subject_type="document_version",
                        subject_id=str(version_id),
                        failed_step=step.value,
                        attempts=failed.attempts,
                        error=message,
                        payload={"completed_steps": [s.value for s in failed.completed_steps]},
                    )
                )
            await uow.commit()

        if exhausted:
            DEAD_LETTERS.add(1, {"step": step.value})
            _logger.error("ingestion_dead_lettered", version_id=str(version_id), step=step.value)

        return IngestionOutcome(
            version_id=version_id,
            completed=False,
            steps_run=steps_run,
            dead_lettered=exhausted,
            error=message,
            retry_in=None if exhausted else RETRY_BASE_SECONDS * 2 ** (failed.attempts - 1),
        )
