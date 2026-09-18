"""The offline generator.

It cannot hallucinate -- every claim is copied verbatim from a retrieved span -- so
what needs testing is whether the claims it copies are *readable*, and whether it
refuses when it should.
"""

from __future__ import annotations

import pytest

from insightlm.documents.domain.spans import PageRect, TextSpan
from insightlm.retrieval.adapters.extractive_generator import ExtractiveGenerator
from insightlm.retrieval.domain.evidence import (
    EvidenceBundle,
    EvidenceQuery,
    RetrievalStrategy,
    RetrievedSpan,
)
from insightlm.shared.domain.ids import (
    new_chunk_id,
    new_document_id,
    new_document_version_id,
)

# A chunk exactly as a PDF yields it: hard line breaks wherever the original wrapped,
# which is mid-sentence more often than not.
PDF_CHUNK = """EXPERIENCE
Led the settlement platform at a payments company, owning reconciliation
and idempotent retries across three payment service providers, sustaining
ten thousand transactions per second at peak.
Reduced p99 latency by forty percent by batching ledger writes and moving
reconciliation off the request path."""


def bundle(text: str) -> EvidenceBundle:
    return EvidenceBundle(
        query=EvidenceQuery(text="q"),
        spans=(
            RetrievedSpan(
                label="C1",
                chunk_id=new_chunk_id(),
                document_id=new_document_id(),
                document_version_id=new_document_version_id(),
                document_title="cv.pdf",
                text=text,
                span=TextSpan(
                    char_start=0,
                    char_end=len(text),
                    rects=(PageRect(page=0, x0=0, y0=0, x1=100, y1=12),),
                ),
                score=0.9,
            ),
        ),
        strategy=RetrievalStrategy.DENSE,
        config_hash="0" * 64,
    )


async def test_claims_are_whole_sentences_not_pdf_lines() -> None:
    """Regression: splitting on newlines produced fragments.

    A claim reading "and idempotent retries across three payment service providers,
    sustaining" is correctly grounded and completely unreadable. It made a working
    system look broken in the UI, which is exactly the kind of defect that never
    shows up in a passing test suite.
    """
    result = await ExtractiveGenerator().generate(
        "What payment systems experience is there?", bundle(PDF_CHUNK)
    )

    assert result.claims
    for claim in result.claims:
        assert not claim.text.startswith("and ")
        assert not claim.text.startswith("reconciliation off")
        # A sentence either ends in terminal punctuation or is the last fragment of
        # the chunk -- never a mid-clause cut.
        assert len(claim.text.split()) > 4


async def test_a_leading_section_heading_is_not_quoted_into_the_claim() -> None:
    result = await ExtractiveGenerator().generate("settlement platform", bundle(PDF_CHUNK))
    assert result.claims
    assert not result.claims[0].text.startswith("EXPERIENCE")
    assert result.claims[0].text.startswith("Led the settlement platform")


async def test_every_claim_cites_the_span_it_was_copied_from() -> None:
    evidence = bundle(PDF_CHUNK)
    result = await ExtractiveGenerator().generate("payment providers", evidence)

    for claim in result.claims:
        assert claim.citation_labels == ("C1",)
        # Copied verbatim: the claim must literally occur in the source.
        flattened = " ".join(PDF_CHUNK.split())
        assert claim.text in flattened


async def test_it_refuses_rather_than_reaching_when_nothing_matches() -> None:
    result = await ExtractiveGenerator().generate(
        "What is their favourite restaurant in Lisbon?", bundle(PDF_CHUNK)
    )
    assert result.refused
    assert result.claims == []


async def test_it_refuses_on_empty_evidence() -> None:
    empty = EvidenceBundle(
        query=EvidenceQuery(text="q"),
        spans=(),
        strategy=RetrievalStrategy.DENSE,
        config_hash="0" * 64,
    )
    result = await ExtractiveGenerator().generate("anything", empty)
    assert result.refused
    assert "No relevant passages" in (result.refusal_reason or "")


@pytest.mark.parametrize("repeats", [2, 3])
async def test_duplicate_sentences_are_not_repeated_as_claims(repeats: int) -> None:
    text = " ".join(["The ledger is reconciled nightly against the provider."] * repeats)
    result = await ExtractiveGenerator().generate("ledger reconciliation", bundle(text))
    assert len(result.claims) == 1
