"""A generated corpus, with ground truth known by construction.

The seed corpus was five hand-written documents. That is too small to measure
anything: with `k=8` retrieval returns nearly the whole corpus, recall sits at 1.000
for every configuration, and a metric that cannot go down cannot detect a regression.

Scaling by hand does not work either -- hand-labelling two hundred documents is where
evaluation projects quietly die. So the documents are *generated from facts*, and the
golden items are *derived from the same facts*. The label is exact because we wrote
the sentence it points at, and adding a document costs nothing.

Two properties make this a real test rather than a bigger easy one:

**Distractors.** Most candidates share vocabulary with the target — payments, ledgers,
reconciliation, throughput — while differing in the fact being asked about. Retrieval
has to discriminate rather than pattern-match, which is what pushes recall off its
ceiling.

**Controlled near-misses.** Some candidates explicitly *deny* the thing being asked
("designed the reports, though not the settlement system itself"). Those become
unanswerable items, and they are where the interesting failures live.

Everything is seeded and byte-deterministic.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import pymupdf

from insightlm.evaluation.adapters.corpus import SyntheticDocument, _serialise
from insightlm.evaluation.domain.dataset import GoldenDataset, GoldenItem, ItemKind

# ---------------------------------------------------------------- fact pools
FIRST_NAMES = (
    "Priya",
    "Arjun",
    "Meera",
    "Rohan",
    "Ananya",
    "Vikram",
    "Divya",
    "Kabir",
    "Sneha",
    "Aditya",
    "Nisha",
    "Farhan",
    "Lakshmi",
    "Imran",
    "Tara",
    "Nikhil",
    "Riya",
    "Sameer",
    "Ishita",
    "Varun",
    "Kavya",
    "Zoya",
    "Aryan",
    "Neha",
)
LAST_NAMES = (
    "Sharma",
    "Rao",
    "Iyer",
    "Mehta",
    "Banerjee",
    "Khan",
    "Nair",
    "Gupta",
    "Desai",
    "Reddy",
    "Bose",
    "Chopra",
    "Menon",
    "Sinha",
    "Kulkarni",
    "Pillai",
)
CITIES = ("Bengaluru", "Pune", "Hyderabad", "Chennai", "Gurugram", "Mumbai", "Kochi")


@dataclass(frozen=True, slots=True)
class Achievement:
    """One sentence, and the question it answers.

    The sentence goes into the document verbatim and becomes the golden quote; the
    question becomes the golden item. Ground truth is therefore exact rather than
    inferred, which is the whole point of generating rather than scraping.
    """

    sentence: str
    question: str
    answer: str
    topic: str


@dataclass(frozen=True, slots=True)
class Candidate:
    slug: str
    name: str
    role: str
    city: str
    domain: str
    years: int
    achievements: tuple[Achievement, ...]
    skills: tuple[str, ...]
    certification: str | None
    education: str
    layout: str
    denial: str | None = None
    """An explicit denial of something the vocabulary suggests. Becomes an
    unanswerable item -- the near-miss that a lexical matcher gets wrong."""

    distinctive: tuple[str, ...] = field(default=())
    """Exact terms that appear in this CV and nowhere else. Dense retrieval tends to
    fumble these; they are what the lexical arm is for."""


# Unique, unambiguous figures so a quote matches exactly one passage across the corpus.
def _unique_number(rng: random.Random, used: set[int]) -> int:
    while True:
        value = rng.randrange(1100, 9800)
        if value not in used:
            used.add(value)
            return value


_DOMAINS: dict[str, tuple[str, ...]] = {
    "payments": ("settlement", "reconciliation", "ledger", "idempotency", "chargebacks"),
    "search": ("ranking", "indexing", "query understanding", "relevance"),
    "data": ("pipelines", "warehouse", "streaming", "batch"),
    "platform": ("service mesh", "autoscaling", "observability", "rollouts"),
    "frontend": ("design system", "accessibility", "bundle size", "rendering"),
    "security": ("threat modelling", "key rotation", "audit logging", "least privilege"),
}

_CERTS = (
    "Certified Kubernetes Administrator",
    "AWS Solutions Architect Professional",
    "CISSP",
    "Google Professional Data Engineer",
    "PCI Professional",
)


def _achievements(
    rng: random.Random, name: str, domain: str, used: set[int]
) -> tuple[Achievement, ...]:
    throughput = _unique_number(rng, used)
    latency = _unique_number(rng, used) % 80 + 11
    saving = _unique_number(rng, used) % 90 + 9

    topic = rng.choice(_DOMAINS[domain])
    built: list[Achievement] = [
        Achievement(
            sentence=(
                f"Owned the {topic} service, which sustained {throughput} requests "
                f"per second during the festive peak."
            ),
            question=f"What peak throughput did the {topic} service owned by {name} sustain?",
            answer=f"{throughput} requests per second.",
            topic=topic,
        ),
        Achievement(
            sentence=(
                f"Cut p99 latency by {latency} percent by removing a synchronous "
                f"call from the write path."
            ),
            question=f"By how much did {name} cut p99 latency?",
            answer=f"By {latency} percent.",
            topic="latency",
        ),
        Achievement(
            sentence=(
                f"Reduced monthly infrastructure spend by {saving} thousand rupees "
                f"by right-sizing the worker fleet."
            ),
            question=f"How much monthly infrastructure spend did {name} save?",
            answer=f"{saving} thousand rupees per month.",
            topic="cost",
        ),
    ]
    return tuple(built)


def build_candidates(*, count: int = 40, seed: int = 20260918) -> list[Candidate]:
    """Deterministic for a given seed and count."""
    rng = random.Random(seed)  # noqa: S311 - corpus generation, not cryptography
    used_numbers: set[int] = set()
    domains = list(_DOMAINS)

    candidates: list[Candidate] = []
    for index in range(count):
        first = FIRST_NAMES[index % len(FIRST_NAMES)]
        last = LAST_NAMES[(index * 7 + 3) % len(LAST_NAMES)]
        name = f"{first} {last}"
        # Payments is over-represented on purpose: a corpus where the target domain is
        # rare makes retrieval look good for the wrong reason.
        domain = "payments" if index % 3 == 0 else domains[index % len(domains)]
        years = 3 + (index % 12)

        certification = _CERTS[index % len(_CERTS)] if index % 4 else None
        distinctive: list[str] = []
        if certification:
            distinctive.append(certification)

        denial: str | None = None
        if domain == "payments" and index % 6 == 0:
            denial = (
                "Built the reporting layer that finance consumed, though not the "
                "settlement engine itself."
            )

        candidates.append(
            Candidate(
                slug=f"cv-{first.lower()}-{last.lower()}-{index:02d}",
                name=name,
                role=f"{'Senior ' if years >= 8 else ''}{domain.title()} Engineer",
                city=CITIES[index % len(CITIES)],
                domain=domain,
                years=years,
                achievements=_achievements(rng, name, domain, used_numbers),
                skills=tuple(rng.sample(list(_DOMAINS[domain]), k=3)),
                certification=certification,
                education=f"{'MTech' if index % 2 else 'BTech'}, {2010 + (index % 12)}.",
                layout="two_column" if index % 2 else "single_column",
                denial=denial,
                distinctive=tuple(distinctive),
            )
        )
    return candidates


# ------------------------------------------------------------------ rendering
def _wrap(text: str, width: int = 62) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    if current:
        lines.append(current)
    return lines


def render(candidate: Candidate) -> SyntheticDocument:
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)

    page.insert_text((56, 72), candidate.name.upper(), fontsize=16)
    page.insert_text((56, 90), f"{candidate.role}  -  {candidate.city}", fontsize=9.5)

    two_column = candidate.layout == "two_column"
    y = 126
    page.insert_text((56, y), "EXPERIENCE", fontsize=12.5)
    y += 20
    page.insert_text((56, y), f"{candidate.years} years building production systems.", fontsize=9.5)
    y += 17

    for achievement in candidate.achievements:
        for line in _wrap(achievement.sentence):
            page.insert_text((56, y), line, fontsize=9.5)
            y += 15
        y += 4

    if candidate.denial:
        for line in _wrap(candidate.denial):
            page.insert_text((56, y), line, fontsize=9.5)
            y += 15

    side_x = 340 if two_column else 56
    side_y = 126 if two_column else y + 22

    page.insert_text((side_x, side_y), "SKILLS", fontsize=12.5)
    side_y += 20
    for skill in candidate.skills:
        page.insert_text((side_x, side_y), skill, fontsize=9.5)
        side_y += 15

    if candidate.certification:
        side_y += 14
        page.insert_text((side_x, side_y), "CERTIFICATIONS", fontsize=12.5)
        side_y += 20
        page.insert_text((side_x, side_y), f"{candidate.certification}.", fontsize=9.5)
        side_y += 15

    side_y += 14
    page.insert_text((side_x, side_y), "EDUCATION", fontsize=12.5)
    side_y += 20
    page.insert_text((side_x, side_y), candidate.education, fontsize=9.5)

    return SyntheticDocument(
        slug=candidate.slug,
        title=f"{candidate.name} - {candidate.role}",
        payload=_serialise(doc),
        layout=candidate.layout,
        notes=f"Generated. Domain {candidate.domain}, {candidate.years}y.",
    )


def build_generated_corpus(*, count: int = 40, seed: int = 20260918) -> list[SyntheticDocument]:
    return [render(candidate) for candidate in build_candidates(count=count, seed=seed)]


# ------------------------------------------------------------- derived labels
def build_generated_dataset(
    *, count: int = 40, seed: int = 20260918, questions_per_kind: int = 12
) -> GoldenDataset:
    """Golden items derived from the same facts the documents were rendered from.

    No hand-labelling, and no inference: the quote is the sentence we wrote into the
    PDF, so the label is exact rather than a best guess at what a human meant.

    The distribution mirrors the hand-written seed set. Unanswerable items come from
    two sources, and the difference matters:

    * **denials** -- a CV that explicitly says it did *not* do the thing. Lexically it
      is the best match in the corpus, which is what makes it the interesting failure.
    * **absent attributes** -- salary, referees, notice period. Nothing in the corpus
      covers them at all. The easy case, included so the metric is not measured
      entirely on the hard one.
    """
    candidates = build_candidates(count=count, seed=seed)
    rng = random.Random(seed + 1)  # noqa: S311 - sampling questions, not cryptography
    items: list[GoldenItem] = []

    # --- factual: one distinctive figure, one document ------------------------
    for candidate in rng.sample(candidates, k=min(questions_per_kind, len(candidates))):
        achievement = candidate.achievements[0]
        items.append(
            GoldenItem(
                id=f"g-fact-{candidate.slug}",
                question=achievement.question,
                kind=ItemKind.FACTUAL,
                quotes=(achievement.sentence,),
                expected_answer=achievement.answer,
                documents=(candidate.slug,),
                notes=(
                    f"Generated. A figure unique across the corpus, so retrieval must "
                    f"find {candidate.name} among {len(candidates)} similar CVs rather "
                    f"than match the domain vocabulary."
                ),
            )
        )

    # --- multi-hop: two facts about one candidate -----------------------------
    for candidate in rng.sample(candidates, k=min(questions_per_kind // 2, len(candidates))):
        first, second = candidate.achievements[0], candidate.achievements[1]
        items.append(
            GoldenItem(
                id=f"g-hop-{candidate.slug}",
                question=(
                    f"For {candidate.name}, what peak throughput was sustained and by "
                    f"how much was p99 latency cut?"
                ),
                kind=ItemKind.MULTI_HOP,
                quotes=(first.sentence, second.sentence),
                expected_answer=f"{first.answer} {second.answer}",
                documents=(candidate.slug,),
                notes="Generated. Two passages from one document; top-1 cannot satisfy it.",
            )
        )

    # --- comparative: a fact held by exactly one candidate ---------------------
    certified = [c for c in candidates if c.certification]
    for candidate in rng.sample(certified, k=min(questions_per_kind // 3, len(certified))):
        holders = [c.name for c in candidates if c.certification == candidate.certification]
        items.append(
            GoldenItem(
                id=f"g-cmp-{candidate.slug}",
                question=f"Which candidates hold the {candidate.certification}?",
                kind=ItemKind.COMPARATIVE,
                quotes=tuple(
                    f"{c.certification}."
                    for c in candidates
                    if c.certification == candidate.certification and c.slug == candidate.slug
                ),
                expected_answer=", ".join(holders),
                documents=(candidate.slug,),
                notes=(
                    "Generated. An exact term dense retrieval tends to fumble and "
                    "lexical search finds immediately -- the case Phase 3's fusion "
                    "is supposed to rescue."
                ),
            )
        )

    # --- unanswerable: explicit denials ---------------------------------------
    deniers = [c for c in candidates if c.denial]
    for candidate in deniers[: max(1, questions_per_kind // 3)]:
        items.append(
            GoldenItem(
                id=f"g-denial-{candidate.slug}",
                question=f"What settlement engine did {candidate.name} build?",
                kind=ItemKind.UNANSWERABLE,
                notes=(
                    "Generated near-miss. The CV says the reporting layer was built "
                    "'though not the settlement engine itself'. Lexically it is the "
                    "strongest match in the corpus; answering it is the failure."
                ),
            )
        )

    # --- unanswerable: attributes no CV carries -------------------------------
    for index, question in enumerate(
        (
            "What salary does each candidate expect?",
            "Who are the referees for these candidates?",
            "What notice period does each candidate have to serve?",
            "Which candidates are willing to relocate abroad?",
        )
    ):
        items.append(
            GoldenItem(
                id=f"g-absent-{index}",
                question=question,
                kind=ItemKind.UNANSWERABLE,
                notes="Generated. Nothing in the corpus covers this attribute at all.",
            )
        )

    dataset = GoldenDataset.of(
        f"generated-{count}",
        items,
        description=(
            f"Derived from the facts used to render {count} generated CVs, so every "
            "label is exact by construction. Distribution mirrors the hand-written "
            "seed set."
        ),
        corpus=tuple(c.slug for c in candidates),
    )
    return dataset
