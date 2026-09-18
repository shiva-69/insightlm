"""The recruiting rules, as executable sentences.

Each test here is one line of docs/05 that a reviewer could otherwise only take on
trust: a human approves the bar before anyone is measured against it; a score is
arithmetic over cited verdicts, never a model's opinion; "not found" is not "lacking".
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from insightlm.recruiting.adapters.judges import OfflineCriterionJudge
from insightlm.recruiting.application.assess import _resolve
from insightlm.recruiting.domain.assessment import (
    AssessmentFlag,
    CriterionJudgment,
    EvidenceRef,
    Verdict,
    aggregate,
)
from insightlm.recruiting.domain.rubric import (
    Criterion,
    CriterionKind,
    Rubric,
    RubricStatus,
)
from insightlm.recruiting.ports.judge import JudgeResult
from insightlm.shared.domain.errors import (
    ConflictError,
    InvariantViolationError,
    ValidationError,
)
from insightlm.shared.domain.ids import (
    new_criterion_id,
    new_requisition_id,
    new_rubric_id,
    new_tenant_id,
    new_user_id,
)
from tests.unit.test_citation_contract import bundle, span

NOW = datetime(2026, 9, 1, tzinfo=UTC)
PROPOSER = new_user_id()
APPROVER = new_user_id()


def criterion(
    label: str,
    *,
    kind: CriterionKind = CriterionKind.STRONG_PREFERENCE,
    weight: float = 0.5,
    disqualifying: bool = False,
) -> Criterion:
    return Criterion(
        id=new_criterion_id(),
        label=label,
        kind=kind,
        weight=weight,
        disqualifying=disqualifying,
    )


def rubric(*criteria: Criterion) -> Rubric:
    return Rubric(
        id=new_rubric_id(),
        tenant_id=new_tenant_id(),
        requisition_id=new_requisition_id(),
        version=1,
        criteria=criteria,
        proposed_by=PROPOSER,
        created_at=NOW,
    )


def judged(c: Criterion, verdict: Verdict, confidence: float = 0.9) -> CriterionJudgment:
    evidence = (
        ()
        if verdict is Verdict.ABSENT
        else (
            EvidenceRef(
                label="C1",
                chunk_id="c",
                document_id="d",
                page=0,
                char_start=0,
                char_end=4,
                quoted_text="text",
            ),
        )
    )
    return CriterionJudgment(
        criterion_id=c.id,
        verdict=verdict,
        confidence=confidence,
        rationale="because",
        evidence=evidence,
    )


# -- approval -------------------------------------------------------------------------


def test_a_proposer_cannot_approve_their_own_rubric() -> None:
    draft = rubric(criterion("Python"))
    with pytest.raises(ConflictError, match="proposed it"):
        draft.approve(by=PROPOSER, at=NOW)


def test_an_owner_may_approve_their_own_rubric() -> None:
    # A team of one has nobody else to ask.
    approved = rubric(criterion("Python")).approve(by=PROPOSER, at=NOW, may_self_approve=True)
    assert approved.is_approved
    assert approved.approved_by == PROPOSER


def test_approval_records_who_and_when() -> None:
    approved = rubric(criterion("Python")).approve(by=APPROVER, at=NOW)
    assert (approved.status, approved.approved_by, approved.approved_at) == (
        RubricStatus.APPROVED,
        APPROVER,
        NOW,
    )


def test_only_a_draft_can_be_approved() -> None:
    approved = rubric(criterion("Python")).approve(by=APPROVER, at=NOW)
    with pytest.raises(ConflictError):
        approved.approve(by=APPROVER, at=NOW)
    with pytest.raises(ConflictError):
        approved.superseded().approve(by=APPROVER, at=NOW)


def test_criteria_labels_must_be_distinct_ignoring_case() -> None:
    with pytest.raises(ValidationError):
        rubric(criterion("Python"), criterion(" python "))


@pytest.mark.parametrize("weight", [0.0, -0.1, 1.01])
def test_weights_live_in_the_unit_interval(weight: float) -> None:
    with pytest.raises(ValidationError):
        criterion("Python", weight=weight)


# -- judgments ------------------------------------------------------------------------


def test_a_finding_without_evidence_cannot_be_constructed() -> None:
    c = criterion("Python")
    with pytest.raises(InvariantViolationError, match="must cite"):
        CriterionJudgment(
            criterion_id=c.id,
            verdict=Verdict.WEAK,
            confidence=0.5,
            rationale="trust me",
            evidence=(),
        )


def test_a_judge_citing_nothing_real_is_downgraded_to_absent() -> None:
    c = criterion("Kubernetes")
    evidence = bundle(span("C1", "Ran Kubernetes clusters in production."))
    result = JudgeResult(
        verdict=Verdict.STRONG,
        confidence=0.95,
        rationale="Clearly an expert.",
        citation_labels=("C7",),  # not retrieved
        model_version="m",
        prompt_version="p",
    )
    judgment = _resolve(c, result, evidence)
    assert judgment.verdict is Verdict.ABSENT
    assert judgment.confidence == 0.0
    assert judgment.evidence == ()
    assert "discarded" in judgment.rationale


def test_resolved_evidence_quotes_the_retrieved_span() -> None:
    c = criterion("Kubernetes")
    text = "Ran Kubernetes clusters in production."
    result = JudgeResult(
        verdict=Verdict.STRONG,
        confidence=0.8,
        rationale="Operated it in production.",
        citation_labels=(" c1 ", "C1"),  # case, whitespace and duplicates tolerated
        model_version="m",
        prompt_version="p",
    )
    judgment = _resolve(c, result, bundle(span("C1", text)))
    assert [e.label for e in judgment.evidence] == ["C1"]
    assert judgment.evidence[0].quoted_text == text


# -- aggregation ----------------------------------------------------------------------


def test_the_score_is_the_weighted_mean_of_verdict_values() -> None:
    python = criterion("Python", weight=1.0)
    kafka = criterion("Kafka", weight=0.5)
    approved = rubric(python, kafka).approve(by=APPROVER, at=NOW)

    score, _ = aggregate(approved, [judged(python, Verdict.STRONG), judged(kafka, Verdict.WEAK)])

    # (1.0 * 1.0 + 0.5 * 0.33) / 1.5
    assert score == pytest.approx(77.7, abs=0.05)


def test_the_same_judgments_always_give_the_same_score() -> None:
    a, b = criterion("A"), criterion("B")
    approved = rubric(a, b).approve(by=APPROVER, at=NOW)
    judgments = [judged(a, Verdict.ADEQUATE), judged(b, Verdict.WEAK)]
    assert aggregate(approved, judgments) == aggregate(approved, list(reversed(judgments)))


def test_an_unapproved_rubric_cannot_produce_a_score() -> None:
    a = criterion("A")
    with pytest.raises(InvariantViolationError):
        aggregate(rubric(a), [judged(a, Verdict.STRONG)])


def test_every_criterion_must_be_judged() -> None:
    a, b = criterion("A"), criterion("B")
    approved = rubric(a, b).approve(by=APPROVER, at=NOW)
    with pytest.raises(InvariantViolationError):
        aggregate(approved, [judged(a, Verdict.STRONG)])


def test_a_missing_must_have_flags_for_review_rather_than_rejecting() -> None:
    must = criterion("Python", kind=CriterionKind.MUST_HAVE, disqualifying=True)
    nice = criterion("Go", kind=CriterionKind.NICE_TO_HAVE)
    approved = rubric(must, nice).approve(by=APPROVER, at=NOW)

    score, flags = aggregate(approved, [judged(must, Verdict.ABSENT), judged(nice, Verdict.STRONG)])

    # Still scored -- nobody is auto-rejected -- but a human must look.
    assert score > 0
    assert AssessmentFlag.MISSING_MUST_HAVE in flags
    assert AssessmentFlag.DISQUALIFYING_ABSENT in flags


def test_weak_and_absent_are_different_verdicts() -> None:
    must = criterion("Python", kind=CriterionKind.MUST_HAVE)
    approved = rubric(must).approve(by=APPROVER, at=NOW)

    weak_score, weak_flags = aggregate(approved, [judged(must, Verdict.WEAK)])
    absent_score, absent_flags = aggregate(approved, [judged(must, Verdict.ABSENT)])

    assert weak_score > absent_score == 0
    assert AssessmentFlag.MISSING_MUST_HAVE not in weak_flags
    assert AssessmentFlag.MISSING_MUST_HAVE in absent_flags


def test_low_mean_confidence_is_flagged() -> None:
    a = criterion("A")
    approved = rubric(a).approve(by=APPROVER, at=NOW)
    _, flags = aggregate(approved, [judged(a, Verdict.STRONG, confidence=0.3)])
    assert AssessmentFlag.LOW_CONFIDENCE in flags


# -- the offline judge ----------------------------------------------------------------


async def test_the_offline_judge_cites_the_passage_it_matched() -> None:
    c = criterion("Kubernetes in production")
    evidence = bundle(
        span("C1", "Enjoys hiking and photography."),
        span("C2", "Operated Kubernetes clusters in production for three years."),
    )
    result = await OfflineCriterionJudge().judge(c, evidence)
    assert result.verdict is Verdict.STRONG
    assert result.citation_labels == ("C2",)


async def test_the_offline_judge_returns_absent_with_no_overlap() -> None:
    c = criterion("Rust")
    result = await OfflineCriterionJudge().judge(c, bundle(span("C1", "Python and Go.")))
    assert result.verdict is Verdict.ABSENT
    assert result.citation_labels == ()


async def test_the_offline_judge_cannot_read_negation() -> None:
    """A documented limitation, pinned so nobody mistakes the baseline for a judge.

    This is the case the model-backed judges exist for, and the reason the UI labels
    offline assessments as a term-overlap baseline.
    """
    c = criterion("Kubernetes")
    result = await OfflineCriterionJudge().judge(
        c, bundle(span("C1", "Has never used Kubernetes."))
    )
    assert result.verdict is not Verdict.ABSENT
