"""A synthetic applicant pool with known strength: `cohort-v1`.

Fifty CVs built from evidence blocks, each block either present or not, so every CV's
true match to the reference rubric is known before any judge reads it. That's what
turns "50 CVs in, ranked shortlist out" from a demo into a check: the ranking can be
compared with the truth.

Deterministic for a given seed. Names are generated from common first and last names
and are not real people.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import pymupdf

FIRST = [
    "Asha",
    "Rahul",
    "Meera",
    "Arjun",
    "Fatima",
    "James",
    "Ngozi",
    "Wei",
    "Sofia",
    "Kiran",
    "Lena",
    "Omar",
    "Priya",
    "Daniel",
    "Aisha",
    "Tomas",
    "Yuki",
    "Ravi",
]
LAST = [
    "Rao",
    "Sharma",
    "Iyer",
    "Khan",
    "Walker",
    "Okafor",
    "Chen",
    "Garcia",
    "Nair",
    "Singh",
    "Fischer",
    "Haddad",
    "Menon",
    "Brown",
    "Ali",
    "Novak",
    "Sato",
    "Kumar",
]

# (criterion, weight, sentences that evidence it). Weights mirror the reference rubric.
BLOCKS = (
    (
        "Python backend services",
        1.0,
        [
            "Built and operated backend services in Python and PostgreSQL.",
            "Wrote FastAPI services handling thousands of requests per second.",
        ],
    ),
    (
        "Payment processing",
        0.7,
        [
            "Owned settlement and reconciliation for card payments.",
            "Integrated payment processing with two acquiring banks.",
        ],
    ),
    (
        "Kubernetes in production",
        0.3,
        [
            "Operated Kubernetes clusters in production.",
            "Ran production workloads on Kubernetes with Helm.",
        ],
    ),
)
FILLER = [
    "Designed marketing pages with a small studio.",
    "Coordinated release notes with product managers.",
    "Wrote internal documentation and onboarding guides.",
    "Organised the team's knowledge-sharing sessions.",
]


@dataclass(frozen=True, slots=True)
class Applicant:
    name: str
    filename: str
    has: tuple[bool, ...]  # per BLOCKS entry
    pdf: bytes

    @property
    def true_score(self) -> float:
        total = sum(w for _, w, _ in BLOCKS)
        return 100 * sum(w for (_, w, _), yes in zip(BLOCKS, self.has, strict=True) if yes) / total


def _pdf(lines: list[str]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    for i, line in enumerate(lines):
        if line:
            size = 16 if i == 0 else (12 if line.isupper() else 10.5)
            page.insert_text((72, 80 + i * 18), line, fontsize=size)
    payload: bytes = doc.tobytes(no_new_id=True, garbage=4, deflate=True)
    doc.close()
    return payload


def build_cohort(count: int = 50, seed: int = 7) -> list[Applicant]:
    rng = random.Random(seed)  # noqa: S311 -- fixture generation, not secrets
    applicants: list[Applicant] = []
    seen: set[str] = set()
    while len(applicants) < count:
        name = f"{rng.choice(FIRST)} {rng.choice(LAST)}"
        if name in seen:
            continue
        seen.add(name)
        has = tuple(rng.random() < 0.5 for _ in BLOCKS)
        body = [
            rng.choice(sentences) for (_, _, sentences), yes in zip(BLOCKS, has, strict=True) if yes
        ]
        body += rng.sample(FILLER, k=2)
        rng.shuffle(body)
        lines = [
            name,
            "Engineer",
            "",
            "EXPERIENCE",
            *body,
            "",
            "EDUCATION",
            "B.Sc Computer Science.",
        ]
        filename = name.lower().replace(" ", "-") + "-cv.pdf"
        applicants.append(Applicant(name, filename, has, _pdf(lines)))
    return applicants
