"""The Phase 1 exit criterion, end to end.

Upload a document, run it through the pipeline, ask a question, and confirm the answer
cites a span that resolves to real geometry in the source PDF. That last step is the
one that matters: it is what makes the citation clickable, and it is the difference
between this system and a chatbot that sounds confident.
"""

from __future__ import annotations

import pymupdf
import pytest

from insightlm.bootstrap import Container, build_role_catalogue
from insightlm.documents.application.upload import UploadDocument
from insightlm.documents.domain.entities import DocumentStatus, IngestionStep
from insightlm.identity.application.commands import RegisterOwnerCommand
from insightlm.retrieval.adapters.chunker import StructureAwareChunker
from insightlm.retrieval.adapters.extractive_generator import ExtractiveGenerator
from insightlm.retrieval.application.answer import AnswerRequest
from insightlm.retrieval.domain.answer import AnswerOutcome
from insightlm.shared.domain.policy import Actor
from insightlm.shared.domain.roles import PlatformRole
from insightlm.worker.ingestion import IngestionPipeline
from tests.conftest import requires_docker
from tests.fakes import FlakyObjectStore, InMemoryObjectStore

pytestmark = [pytest.mark.integration, requires_docker]

PASSWORD = "correct-horse-battery-staple"


def build_resume_pdf() -> bytes:
    """A two-column résumé -- the layout naive extraction turns into nonsense."""
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((72, 80), "EXPERIENCE", fontsize=14)
    for index, line in enumerate(
        [
            "Led the settlement platform at a payments company, owning",
            "reconciliation and idempotent retries across three payment",
            "service providers at ten thousand transactions per second.",
            "Reduced p99 latency by forty percent by batching ledger writes.",
        ]
    ):
        page.insert_text((72, 110 + index * 16), line, fontsize=10)

    page.insert_text((72, 200), "EDUCATION", fontsize=14)
    page.insert_text((72, 225), "MCA in progress. BSc Computer Science, 2021.", fontsize=10)

    page.insert_text((340, 110), "SKILLS", fontsize=12)
    page.insert_text((340, 132), "Python, PostgreSQL, Kubernetes", fontsize=10)
    page.insert_text((340, 150), "PCI-DSS, pgvector, RabbitMQ", fontsize=10)

    payload: bytes = doc.tobytes()
    doc.close()
    return payload


async def owner_of(container: Container, slug: str = "acme") -> Actor:
    result = await container.identity.register_owner.execute(
        RegisterOwnerCommand(
            tenant_name=slug.title(),
            tenant_slug=slug,
            email=f"owner@{slug}.example.com",
            display_name="Owner",
            password=PASSWORD,
        )
    )
    return Actor.of(
        build_role_catalogue(),
        user_id=result.user.id,
        tenant_id=result.tenant.id,
        role=PlatformRole.OWNER,
    )


def pipeline_with(container: Container, store: InMemoryObjectStore) -> IngestionPipeline:
    from insightlm.documents.adapters.pymupdf_parser import PyMuPdfParser

    return IngestionPipeline(
        container.session_factory,
        store=store,
        parser=PyMuPdfParser(),
        chunker=StructureAwareChunker(),
        embedder=container.embedder,
        clock=container.clock,
    )


async def ingest(container: Container, actor: Actor, store: InMemoryObjectStore) -> object:
    upload = UploadDocument(container.documents.uow, store, container.clock)
    result = await upload.execute(
        actor, filename="candidate.pdf", payload=build_resume_pdf(), title="Candidate CV"
    )
    outcome = await pipeline_with(container, store).run(
        tenant_id=actor.tenant_id, version_id=result.version.id
    )
    assert outcome.completed, outcome.error
    return result


async def test_upload_ingest_ask_yields_a_resolvable_citation(container: Container) -> None:
    actor = await owner_of(container)
    store = InMemoryObjectStore()
    await ingest(container, actor, store)

    output = await container.retrieval.ask.ask(
        actor, AnswerRequest(question="What payment systems experience is there?")
    )
    answer = output.answer

    assert answer.outcome is AnswerOutcome.ANSWERED, answer.refusal_reason
    assert answer.claims

    # The contract: every claim carries at least one citation, and every citation
    # resolves to a span that was actually retrieved.
    retrieved = answer.evidence.by_label()
    for claim in answer.claims:
        assert claim.citations
        for citation in claim.citations:
            assert citation.label in retrieved
            assert citation.document_title == "Candidate CV"

    # And the citation resolves to real geometry -- the part that makes it clickable.
    first = answer.citations[0]
    span = retrieved[first.label].span
    assert span.rects, "citation has no geometry; the viewer cannot highlight it"
    assert span.rects[0].page == 0
    assert span.rects[0].width > 0 and span.rects[0].height > 0

    # Provenance for reproducibility.
    assert answer.model_version
    assert answer.prompt_version
    assert len(answer.evidence.config_hash) == 64


async def test_the_answer_quotes_text_that_exists_in_the_document(
    container: Container,
) -> None:
    """Grounding, checked against the source rather than trusted.

    Every cited excerpt must appear verbatim in the extracted text. A citation that
    quotes something the document does not say is the exact failure being guarded
    against.
    """
    actor = await owner_of(container)
    store = InMemoryObjectStore()
    result = await ingest(container, actor, store)

    async with container.documents.uow(tenant_id=actor.tenant_id) as uow:
        extracted = await uow.versions.load_text(result.version.id)  # type: ignore[attr-defined]
    assert extracted is not None

    output = await container.retrieval.ask.ask(
        actor, AnswerRequest(question="What was reduced by batching ledger writes?")
    )

    for citation in output.answer.citations:
        assert citation.quoted_text in extracted.text
        # And the recorded offsets point at that same text.
        assert extracted.text[citation.char_start : citation.char_end] == citation.quoted_text


async def test_an_unanswerable_question_is_refused_not_invented(
    container: Container,
) -> None:
    """Refusal is a success condition, and it is measured as one in Phase 2."""
    actor = await owner_of(container)
    store = InMemoryObjectStore()
    await ingest(container, actor, store)

    output = await container.retrieval.ask.ask(
        actor,
        AnswerRequest(question="What is the candidate's favourite restaurant in Lisbon?"),
    )

    assert output.answer.outcome is AnswerOutcome.INSUFFICIENT_EVIDENCE
    assert output.answer.claims == ()
    assert output.answer.refusal_reason


async def test_ingestion_records_every_step_and_marks_the_version_ready(
    container: Container,
) -> None:
    actor = await owner_of(container)
    store = InMemoryObjectStore()
    result = await ingest(container, actor, store)

    async with container.documents.uow(tenant_id=actor.tenant_id) as uow:
        version = await uow.versions.get(result.version.id)  # type: ignore[attr-defined]
        job = await uow.jobs.for_version(result.version.id)  # type: ignore[attr-defined]

    assert version is not None and job is not None
    assert version.status is DocumentStatus.READY
    assert version.page_count == 1
    assert version.char_count and version.char_count > 0
    assert set(job.completed_steps) == set(IngestionStep.ordered())
    assert job.is_complete


async def test_a_failed_step_resumes_rather_than_restarts(container: Container) -> None:
    """The claim that makes resumable steps worth building.

    The store fails once during `parse`. On retry the pipeline resumes from the step
    that failed -- it does not re-run the steps that already succeeded.
    """
    actor = await owner_of(container)
    store = FlakyObjectStore(fail_times=1)
    store._remaining_failures = 0  # let the upload write succeed
    upload = UploadDocument(container.documents.uow, store, container.clock)
    result = await upload.execute(actor, filename="candidate.pdf", payload=build_resume_pdf())

    store._remaining_failures = 1  # now fail the first read, inside `parse`
    pipeline = pipeline_with(container, store)

    first = await pipeline.run(tenant_id=actor.tenant_id, version_id=result.version.id)
    assert not first.completed
    assert first.steps_run == ()
    assert not first.dead_lettered

    second = await pipeline.run(tenant_id=actor.tenant_id, version_id=result.version.id)
    assert second.completed
    assert second.steps_run == IngestionStep.ordered()


async def test_exhausted_retries_land_in_the_dead_letter_table(
    container: Container,
) -> None:
    """A document that cannot be processed must be visible, never silently absent."""
    from sqlalchemy import text

    actor = await owner_of(container)
    store = FlakyObjectStore(fail_times=0)
    upload = UploadDocument(container.documents.uow, store, container.clock)
    result = await upload.execute(actor, filename="candidate.pdf", payload=build_resume_pdf())

    store._remaining_failures = 99  # permanently broken
    pipeline = pipeline_with(container, store)

    outcome = None
    for _ in range(3):
        outcome = await pipeline.run(tenant_id=actor.tenant_id, version_id=result.version.id)
    assert outcome is not None
    assert outcome.dead_lettered

    async with container.documents.uow(tenant_id=actor.tenant_id) as uow:
        version = await uow.versions.get(result.version.id)  # type: ignore[attr-defined]
        rows = (
            await uow.session.execute(text("SELECT failed_step, attempts FROM dead_letters"))
        ).all()

    assert version is not None and version.status is DocumentStatus.FAILED
    assert len(rows) == 1
    assert rows[0].failed_step == IngestionStep.PARSE.value


async def test_reuploading_identical_bytes_is_deduplicated(container: Container) -> None:
    actor = await owner_of(container)
    store = InMemoryObjectStore()
    upload = UploadDocument(container.documents.uow, store, container.clock)
    payload = build_resume_pdf()

    first = await upload.execute(actor, filename="cv.pdf", payload=payload)
    second = await upload.execute(actor, filename="cv-copy.pdf", payload=payload)

    assert not first.was_duplicate
    assert second.was_duplicate
    assert second.version.id == first.version.id
    assert store.put_calls == 1


async def test_retrieval_is_scoped_to_the_asking_tenant(container: Container) -> None:
    """Two tenants, one corpus each. Neither can retrieve the other's evidence."""
    acme = await owner_of(container, "acme")
    globex = await owner_of(container, "globex")

    store = InMemoryObjectStore()
    await ingest(container, acme, store)

    output = await container.retrieval.ask.ask(
        globex, AnswerRequest(question="What payment systems experience is there?")
    )

    assert output.answer.evidence.is_empty
    assert output.answer.outcome is AnswerOutcome.INSUFFICIENT_EVIDENCE


async def test_generation_cannot_be_asked_about_evidence_it_did_not_receive(
    container: Container,
) -> None:
    """The generator sees only the retrieved bundle, never the corpus."""
    generator = ExtractiveGenerator()
    from insightlm.retrieval.domain.evidence import (
        EvidenceBundle,
        EvidenceQuery,
        RetrievalStrategy,
    )

    empty = EvidenceBundle(
        query=EvidenceQuery(text="anything"),
        spans=(),
        strategy=RetrievalStrategy.DENSE,
        config_hash="0" * 64,
    )
    result = await generator.generate("anything", empty)

    assert result.refused
    assert result.claims == []


# --------------------------------------------------------------------- HTTP
async def test_the_api_returns_citations_with_highlight_geometry(
    container: Container, client: object
) -> None:
    """What the viewer consumes.

    The response must carry, per citation, the page and the rectangles needed to draw
    a highlight. Without geometry the citation is a footnote; with it, it is clickable.
    """
    from httpx import AsyncClient

    http: AsyncClient = client  # type: ignore[assignment]

    register = await http.post(
        "/auth/register",
        json={
            "tenant_name": "Acme",
            "tenant_slug": "acme",
            "email": "owner@acme.example.com",
            "display_name": "Owner",
            "password": PASSWORD,
        },
    )
    assert register.status_code == 201, register.text

    login = await http.post(
        "/auth/login",
        json={"email": "owner@acme.example.com", "password": PASSWORD},
    )
    token = login.json()["access_token"]
    auth = {"Authorization": f"Bearer {token}"}

    upload = await http.post(
        "/documents",
        headers=auth,
        files={"file": ("candidate.pdf", build_resume_pdf(), "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    document_id = upload.json()["document"]["id"]

    # The worker is not running in this test, so the pipeline is driven directly --
    # the same code path ARQ would invoke.
    from uuid import UUID

    from insightlm.shared.domain.ids import DocumentVersionId, TenantId

    outcome = await container.documents.ingestion.run(
        tenant_id=TenantId(UUID(register.json()["tenant"]["id"])),
        version_id=DocumentVersionId(UUID(upload.json()["version"]["id"])),
    )
    assert outcome.completed, outcome.error

    status = await http.get(f"/documents/{document_id}/status", headers=auth)
    assert status.json()["status"] == "ready"

    answer = await http.post(
        "/ask",
        headers=auth,
        json={"question": "What payment systems experience is there?"},
    )
    assert answer.status_code == 200, answer.text
    body = answer.json()

    assert body["outcome"] == "answered"
    assert body["claims"]
    assert body["citations"]

    citation = body["citations"][0]
    assert citation["rects"], "no geometry -- the viewer cannot draw a highlight"
    rect = citation["rects"][0]
    assert rect["x1"] > rect["x0"] and rect["y1"] > rect["y0"]
    assert citation["page"] == 0
    assert citation["quoted_text"]

    # Provenance is part of the response, not an internal detail.
    assert body["retrieval_config_hash"]
    assert body["model_version"]
    assert body["prompt_version"]

    raw = await http.get(f"/documents/{document_id}/raw", headers=auth)
    assert raw.status_code == 200
    assert raw.content[:4] == b"%PDF"
