"""Candidates, applications, and the evidence-bounded assessment.

Two rules from docs/04, made structural:

**The model never emits a score.** It answers narrow questions -- does this evidence
support this one criterion? -- with a verdict and the citations behind it. The score is
arithmetic over those verdicts, done here, in code that can be read, tested and
re-run. Change the weights and every historical score can be recomputed and explained.

**Absent is not weak.** "No evidence found" is a retrieval outcome, not a judgement of
the candidate. It may mean the skill is missing, or that the CV is badly formatted, or
that OCR failed. Conflating the two is how good candidates get silently dropped, so
they are separate verdicts with separate consequences.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from insightlm.documents.domain.spans import PageRect
from insightlm.recruiting.domain.rubric import CriterionKind, Rubric
from insightlm.shared.domain.errors import InvariantViolationError, ValidationError
from insightlm.shared.domain.ids import (
    ApplicationId,
    AssessmentId,
    CandidateId,
    CriterionId,
    DocumentId,
    RequisitionId,
    RubricId,
    TenantId,
    UserId,
)


@dataclass(frozen=True, slots=True)
class Candidate:
    id: CandidateId
    tenant_id: TenantId
    name: str
    email: str | None
    document_ids: tuple[DocumentId, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValidationError("a candidate needs a name")
        if not self.document_ids:
            raise ValidationError("a candidate needs at least one document to be assessed")


class Stage(StrEnum):
    APPLIED = "applied"
    SCREENED = "screened"
    INTERVIEW = "interview"
    OFFER = "offer"
    HIRED = "hired"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class Application:
    id: ApplicationId
    tenant_id: TenantId
    candidate_id: CandidateId
    requisition_id: RequisitionId
    stage: Stage
    created_at: datetime
    # When the application entered its current stage -- what ageing and SLAs run from.
    stage_entered_at: datetime


class Verdict(StrEnum):
    STRONG = "strong"
    ADEQUATE = "adequate"
    WEAK = "weak"
    ABSENT = "absent"

    @property
    def value_score(self) -> float:
        # Fixed, published, and in code. These are the numbers a candidate could ask
        # to see, and they must be the same numbers every time.
        return {"strong": 1.0, "adequate": 0.66, "weak": 0.33, "absent": 0.0}[self.value]


@dataclass(frozen=True, slots=True)
class EvidenceRef:
    """A citation into the candidate's documents. Enough to draw a highlight.

    A snapshot, not a pointer: the quote, title and geometry are copied at assessment
    time, so an assessment still shows exactly what it rested on after the document is
    re-ingested or re-chunked.
    """

    label: str
    chunk_id: str
    document_id: str
    page: int
    char_start: int
    char_end: int
    quoted_text: str
    document_title: str = ""
    rects: tuple[PageRect, ...] = ()


@dataclass(frozen=True, slots=True)
class CriterionJudgment:
    criterion_id: CriterionId
    verdict: Verdict
    confidence: float
    rationale: str
    evidence: tuple[EvidenceRef, ...]

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValidationError("confidence must be in [0, 1]")
        # The citation contract, applied to judgments: a positive finding about a
        # person must point at the text it rests on.
        if self.verdict is not Verdict.ABSENT and not self.evidence:
            raise InvariantViolationError(
                "a judgment that finds evidence must cite it",
                detail={"verdict": self.verdict.value},
            )


class AssessmentFlag(StrEnum):
    MISSING_MUST_HAVE = "missing_must_have"
    DISQUALIFYING_ABSENT = "disqualifying_absent"
    LOW_CONFIDENCE = "low_confidence"
    """Routes to mandatory human review rather than producing a confident-looking
    number from uncertain parts."""


LOW_CONFIDENCE_THRESHOLD = 0.55


@dataclass(frozen=True, slots=True)
class Assessment:
    id: AssessmentId
    tenant_id: TenantId
    application_id: ApplicationId
    rubric_id: RubricId
    rubric_version: int
    judgments: tuple[CriterionJudgment, ...]
    score: float
    flags: tuple[AssessmentFlag, ...]
    assessed_by: UserId
    created_at: datetime
    model_version: str
    prompt_version: str
    retrieval_config_hash: str
    # What the judge was not shown -- part of reproducing and contesting a score.
    redaction_profile: str = "none"

    @property
    def needs_human_review(self) -> bool:
        return bool(self.flags)


def aggregate(
    rubric: Rubric, judgments: list[CriterionJudgment]
) -> tuple[float, tuple[AssessmentFlag, ...]]:
    """Weighted mean of verdict values, 0-100, plus the flags a human must see.

    Deterministic and model-free by design. The same judgments under the same rubric
    always produce the same score, which is what makes "why did this candidate score
    62?" answerable line by line.

    Flags never reject anyone. A missing must-have or an absent disqualifying
    criterion routes the application to a person, because "the evidence was not
    found" and "the candidate lacks this" are different claims and only a human
    reading the CV can tell them apart.
    """
    if not rubric.is_approved:
        raise InvariantViolationError("cannot aggregate against an unapproved rubric")

    by_criterion = {j.criterion_id: j for j in judgments}
    missing = [c.label for c in rubric.criteria if c.id not in by_criterion]
    if missing:
        raise InvariantViolationError(
            "every criterion must be judged before aggregation", detail={"missing": missing}
        )

    total_weight = sum(c.weight for c in rubric.criteria)
    score = (
        100.0
        * sum(c.weight * by_criterion[c.id].verdict.value_score for c in rubric.criteria)
        / total_weight
    )

    flags: list[AssessmentFlag] = []
    for criterion in rubric.criteria:
        verdict = by_criterion[criterion.id].verdict
        if verdict is not Verdict.ABSENT:
            continue
        if criterion.kind is CriterionKind.MUST_HAVE:
            flags.append(AssessmentFlag.MISSING_MUST_HAVE)
        if criterion.disqualifying:
            flags.append(AssessmentFlag.DISQUALIFYING_ABSENT)

    if statistics.fmean(j.confidence for j in judgments) < LOW_CONFIDENCE_THRESHOLD:
        flags.append(AssessmentFlag.LOW_CONFIDENCE)

    return round(score, 1), tuple(dict.fromkeys(flags))
