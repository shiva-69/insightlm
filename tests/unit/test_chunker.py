"""Structure-aware chunking, including the contextual-enrichment arm.

The enrichment tests encode a failure the evaluation harness found and the test
suite could not: on a 40-CV corpus, multi-hop recall was 0.000 for *both* a hashing
embedder and a real semantic one. Two very different embedders failing identically
ruled out the embedder, and the cause turned out to be that the passage describing an
achievement never contains the name of the person who achieved it -- the name lives in
the document's header chunk.
"""

from __future__ import annotations

import asyncio

import pymupdf
import pytest

from insightlm.documents.adapters.pymupdf_parser import PyMuPdfParser
from insightlm.documents.domain.entities import MediaType
from insightlm.retrieval.adapters.chunker import StructureAwareChunker
from insightlm.shared.domain.ids import new_document_version_id, new_tenant_id


def build_cv() -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    page.insert_text((56, 72), "KAVYA PILLAI", fontsize=16)
    page.insert_text((56, 110), "EXPERIENCE", fontsize=12.5)
    page.insert_text((56, 132), "Owned the streaming service, which sustained 5466", fontsize=9.5)
    page.insert_text((56, 147), "requests per second during the festive peak.", fontsize=9.5)
    page.insert_text((56, 190), "EDUCATION", fontsize=12.5)
    page.insert_text((56, 212), "MTech, 2016.", fontsize=9.5)
    payload: bytes = doc.tobytes()
    doc.close()
    return payload


def chunks_of(*, enrich: bool):
    parsed = asyncio.run(PyMuPdfParser().parse(build_cv(), media_type=MediaType.PDF))
    return StructureAwareChunker(enrich_with_title=enrich).chunk(
        parsed,
        tenant_id=new_tenant_id(),
        document_version_id=new_document_version_id(),
        document_title="Kavya Pillai - Data Engineer",
    )


def test_without_enrichment_the_achievement_passage_never_names_the_person() -> None:
    """The failure, reproduced.

    A question asking about Kavya's throughput has nothing to match against: the
    passage that answers it does not contain her name.
    """
    experience = next(c for c in chunks_of(enrich=False) if "5466" in c.text)
    assert "Kavya" not in experience.embedding_text


def test_enrichment_puts_the_document_identity_into_what_gets_embedded() -> None:
    experience = next(c for c in chunks_of(enrich=True) if "5466" in c.text)
    assert "Kavya Pillai" in experience.embedding_text
    assert "5466" in experience.embedding_text


def test_the_header_is_embedded_but_never_cited() -> None:
    """The citation must point at the passage a reader needs to see.

    Quoting a header we synthesised back at them would be citing our own scaffolding.
    """
    experience = next(c for c in chunks_of(enrich=True) if "5466" in c.text)
    assert "Kavya Pillai" in experience.embedding_text
    assert "Kavya Pillai - Data Engineer" not in experience.text


def test_sections_remain_hard_boundaries() -> None:
    labels = {c.section_label for c in chunks_of(enrich=True)}
    assert any("EXPERIENCE" in label for label in labels)
    assert any("EDUCATION" in label for label in labels)
    # No chunk blends the two.
    for chunk in chunks_of(enrich=True):
        assert not ("5466" in chunk.text and "MTech" in chunk.text)


def test_geometry_survives_chunking() -> None:
    """Without rectangles the citation cannot be drawn."""
    experience = next(c for c in chunks_of(enrich=True) if "5466" in c.text)
    assert experience.span.rects
    assert experience.span.rects[0].width > 0


@pytest.mark.parametrize("enrich", [True, False])
def test_char_offsets_point_at_the_chunk_text(enrich: bool) -> None:
    """The offsets resolve against the extracted text, not the enriched text."""
    parsed = asyncio.run(PyMuPdfParser().parse(build_cv(), media_type=MediaType.PDF))
    for chunk in StructureAwareChunker(enrich_with_title=enrich).chunk(
        parsed,
        tenant_id=new_tenant_id(),
        document_version_id=new_document_version_id(),
        document_title="Kavya Pillai - Data Engineer",
    ):
        assert parsed.text[chunk.span.char_start : chunk.span.char_end] == chunk.text
