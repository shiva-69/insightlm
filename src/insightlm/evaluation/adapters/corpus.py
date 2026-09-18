"""A synthetic corpus, generated deterministically.

Synthetic by choice, not convenience. Real résumés are personal data, and a hiring
project that trains and evaluates on scraped CVs has undermined its own argument
before it starts. Generating the corpus also means the ground truth is *known* rather
than inferred — we know exactly which document contains which fact, which is what
makes quote-based labelling reliable.

Everything is seeded, so the same corpus is produced on every machine and an
evaluation run is reproducible. The documents deliberately include the awkward cases:
two-column layouts that naive extraction interleaves, a sparse CV with almost no
prose, and one with no extractable text at all to exercise the failure path.
"""

from __future__ import annotations

from dataclasses import dataclass

import pymupdf


@dataclass(frozen=True, slots=True)
class SyntheticDocument:
    slug: str
    title: str
    payload: bytes
    layout: str
    notes: str


def _page(doc: pymupdf.Document) -> pymupdf.Page:
    return doc.new_page(width=595, height=842)  # A4 in points


def _serialise(doc: pymupdf.Document) -> bytes:
    """Produce the same bytes every time.

    PyMuPDF stamps a fresh document ID and a creation timestamp on each save, so the
    obvious `doc.tobytes()` yields different bytes for identical content. That breaks
    two things at once: the ingestion idempotency key is a content checksum, so
    regenerating the corpus would re-ingest everything instead of deduplicating; and
    an evaluation run stops being reproducible at the only level that matters.

    Clearing the metadata and suppressing the new ID fixes both.
    """
    doc.set_metadata({})
    payload: bytes = doc.tobytes(no_new_id=True, garbage=4, deflate=True)
    doc.close()
    return payload


def _single_column(title: str, subtitle: str, sections: list[tuple[str, list[str]]]) -> bytes:
    doc = pymupdf.open()
    page = _page(doc)
    y = 72
    page.insert_text((56, y), title, fontsize=17)
    y += 18
    page.insert_text((56, y), subtitle, fontsize=9.5)
    y += 34

    for heading, lines in sections:
        page.insert_text((56, y), heading, fontsize=12.5)
        y += 20
        for line in lines:
            page.insert_text((56, y), line, fontsize=9.5)
            y += 15
        y += 16

    return _serialise(doc)


def _two_column(
    title: str,
    subtitle: str,
    left: list[tuple[str, list[str]]],
    right: list[tuple[str, list[str]]],
) -> bytes:
    """The layout that breaks naive extraction.

    Two columns rendered as separate text blocks. An extractor that walks glyphs in
    raw position order interleaves them into plausible-looking nonsense; one that
    respects block structure does not. This is here so the difference is measurable.
    """
    doc = pymupdf.open()
    page = _page(doc)
    page.insert_text((56, 72), title, fontsize=17)
    page.insert_text((56, 90), subtitle, fontsize=9.5)

    for x, column in ((56, left), (330, right)):
        y = 126
        for heading, lines in column:
            page.insert_text((x, y), heading, fontsize=12.5)
            y += 20
            for line in lines:
                page.insert_text((x, y), line, fontsize=9.5)
                y += 15
            y += 16

    return _serialise(doc)


def _image_only() -> bytes:
    """A page with no extractable text, standing in for a scan.

    Ingestion must fail this visibly -- a résumé that silently yields nothing is a
    candidate who silently disappears from the pipeline, which is the failure this
    project exists to make impossible.
    """
    doc = pymupdf.open()
    page = _page(doc)
    page.draw_rect(pymupdf.Rect(56, 72, 540, 400), color=(0.8, 0.8, 0.8), fill=(0.9, 0.9, 0.9))
    return _serialise(doc)


def build_corpus() -> list[SyntheticDocument]:
    """The evaluation corpus.

    Byte-identical on every call and every machine, which is what lets a run be
    reproduced and lets re-generating the corpus deduplicate rather than re-ingest.
    """
    documents: list[SyntheticDocument] = []

    documents.append(
        SyntheticDocument(
            slug="cv-priya-payments",
            title="Priya Sharma — Senior Backend Engineer",
            layout="two_column",
            notes="Senior, payments. The two-column layout naive extraction interleaves.",
            payload=_two_column(
                "PRIYA SHARMA",
                "Senior Backend Engineer  ·  Bengaluru",
                left=[
                    (
                        "EXPERIENCE",
                        [
                            "Led the settlement platform at a payments company, owning",
                            "reconciliation and idempotent retries across three payment",
                            "service providers, sustaining ten thousand transactions",
                            "per second at peak.",
                            "Reduced p99 latency by forty percent by batching ledger",
                            "writes and moving reconciliation off the request path.",
                            "Introduced an append-only audit trail so every balance",
                            "change could be traced to the transaction that caused it.",
                        ],
                    ),
                    (
                        "EDUCATION",
                        [
                            "MCA in progress, expected 2027.",
                            "BSc Computer Science, 2021.",
                        ],
                    ),
                ],
                right=[
                    (
                        "SKILLS",
                        [
                            "Python, PostgreSQL, pgvector",
                            "Kubernetes, Docker, Redis",
                            "PCI-DSS, idempotency, ledgers",
                        ],
                    ),
                    (
                        "CERTIFICATIONS",
                        [
                            "Certified Kubernetes Administrator, 2024.",
                        ],
                    ),
                ],
            ),
        )
    )

    documents.append(
        SyntheticDocument(
            slug="cv-arjun-frontend",
            title="Arjun Rao — Frontend Engineer",
            layout="single_column",
            notes="Mid-level, frontend. No payments experience — the contrast case.",
            payload=_single_column(
                "ARJUN RAO",
                "Frontend Engineer  ·  Pune",
                [
                    (
                        "EXPERIENCE",
                        [
                            "Built the design system used across four product teams,",
                            "cutting new-screen delivery time from days to hours.",
                            "Migrated a legacy jQuery dashboard to React, reducing",
                            "bundle size by sixty percent.",
                            "Owned accessibility, taking the checkout flow to WCAG",
                            "2.2 AA compliance.",
                        ],
                    ),
                    (
                        "SKILLS",
                        [
                            "TypeScript, React, Next.js, Tailwind",
                            "Playwright, accessibility auditing",
                        ],
                    ),
                    ("EDUCATION", ["BTech Information Technology, 2019."]),
                ],
            ),
        )
    )

    documents.append(
        SyntheticDocument(
            slug="cv-meera-data",
            title="Meera Iyer — Data Engineer",
            layout="single_column",
            notes="Senior, data. Shares vocabulary with payments without the experience.",
            payload=_single_column(
                "MEERA IYER",
                "Data Engineer  ·  Hyderabad",
                [
                    (
                        "EXPERIENCE",
                        [
                            "Built the streaming pipeline that feeds the risk models,",
                            "processing four billion events per day on Kafka.",
                            "Owned the data warehouse migration to a columnar store,",
                            "cutting nightly batch time from nine hours to forty minutes.",
                            "Designed the reconciliation reports used by the finance",
                            "team, though not the settlement system itself.",
                        ],
                    ),
                    (
                        "SKILLS",
                        ["Kafka, Spark, dbt, Airflow", "Python, Scala, SQL"],
                    ),
                    ("EDUCATION", ["MSc Statistics, 2017."]),
                ],
            ),
        )
    )

    documents.append(
        SyntheticDocument(
            slug="jd-staff-payments",
            title="Job description — Staff Engineer, Payments",
            layout="single_column",
            notes="Not a CV. Enables cross-document comparative questions.",
            payload=_single_column(
                "STAFF ENGINEER, PAYMENTS",
                "Requisition PAY-114  ·  Remote (India)",
                [
                    (
                        "REQUIREMENTS",
                        [
                            "Seven or more years building backend systems, of which at",
                            "least three on payments, ledgers or settlement.",
                            "Demonstrated experience with idempotency and exactly-once",
                            "semantics under partial failure.",
                            "Familiarity with PCI-DSS obligations in a production system.",
                        ],
                    ),
                    (
                        "NICE TO HAVE",
                        [
                            "Experience operating Kubernetes in a regulated environment.",
                            "Exposure to real-time risk or fraud systems.",
                        ],
                    ),
                ],
            ),
        )
    )

    documents.append(
        SyntheticDocument(
            slug="cv-sparse-scan",
            title="Unreadable scan",
            layout="image_only",
            notes=(
                "No extractable text. Ingestion must fail this visibly rather than "
                "index an empty document."
            ),
            payload=_image_only(),
        )
    )

    return documents
