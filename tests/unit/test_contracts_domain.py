"""Contract review's rules: approval, citations, and the risk summary, without a database."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from insightlm.contracts.adapters.judges import OfflineClauseJudge
from insightlm.contracts.domain.ids import new_playbook_id, new_position_id
from insightlm.contracts.domain.playbook import Playbook, PlaybookStatus, Position, Severity
from insightlm.contracts.domain.review import (
    ClauseEvidence,
    Finding,
    PositionFinding,
    ReviewFlag,
    summarise,
)
from insightlm.shared.domain.errors import ConflictError, InvariantViolationError, ValidationError
from insightlm.shared.domain.ids import new_tenant_id, new_user_id
from tests.unit.test_citation_contract import bundle, span

NOW = datetime(2026, 11, 1, tzinfo=UTC)
AUTHOR, COUNSEL = new_user_id(), new_user_id()

LIABILITY = Position(
    new_position_id(),
    "Limitation of liability",
    "Aggregate liability capped at 12 months' fees, with confidentiality uncapped.",
    Severity.CRITICAL,
    "liability, cap, aggregate",
)
TERMINATION = Position(
    new_position_id(),
    "Termination for convenience",
    "Either party may terminate on 30 days' written notice.",
    Severity.MAJOR,
    "terminate, notice",
)
GOVERNING_LAW = Position(
    new_position_id(), "Governing law", "Laws of England and Wales.", Severity.MINOR
)


def playbook(status: PlaybookStatus = PlaybookStatus.DRAFT) -> Playbook:
    approved = status is PlaybookStatus.APPROVED
    return Playbook(
        id=new_playbook_id(),
        tenant_id=new_tenant_id(),
        name="Standard MSA",
        version=1,
        positions=(LIABILITY, TERMINATION, GOVERNING_LAW),
        proposed_by=AUTHOR,
        created_at=NOW,
        status=status,
        approved_by=COUNSEL if approved else None,
        approved_at=NOW if approved else None,
    )


EVIDENCE = (ClauseEvidence("C1", "chunk", "doc", 0, 0, 10, "quoted"),)


def finding(position: Position, kind: Finding, confidence: float = 0.9) -> PositionFinding:
    return PositionFinding(
        position.id, kind, confidence, "because", () if kind is Finding.MISSING else EVIDENCE
    )


def test_the_author_cannot_approve_their_own_playbook() -> None:
    with pytest.raises(ConflictError):
        playbook().approve(by=AUTHOR, at=NOW)
    approved = playbook().approve(by=COUNSEL, at=NOW)
    assert approved.is_approved and approved.approved_by == COUNSEL


def test_only_a_draft_can_be_approved() -> None:
    with pytest.raises(ConflictError):
        playbook(PlaybookStatus.APPROVED).approve(by=COUNSEL, at=NOW)


def test_two_positions_on_one_clause_are_refused() -> None:
    with pytest.raises(ValidationError):
        Playbook(new_playbook_id(), new_tenant_id(), "x", 1, (LIABILITY, LIABILITY), AUTHOR, NOW)


def test_a_finding_about_the_text_must_cite_the_text() -> None:
    with pytest.raises(InvariantViolationError):
        PositionFinding(LIABILITY.id, Finding.DEVIATES, 0.9, "no", evidence=())
    # Missing cites nothing, by definition.
    assert PositionFinding(LIABILITY.id, Finding.MISSING, 0.9, "silent", ()).evidence == ()


def test_a_critical_gap_needs_counsel_and_a_minor_one_does_not() -> None:
    approved = playbook(PlaybookStatus.APPROVED)
    critical = summarise(
        approved,
        [
            finding(LIABILITY, Finding.DEVIATES),
            finding(TERMINATION, Finding.MEETS),
            finding(GOVERNING_LAW, Finding.MEETS),
        ],
    )
    assert critical.flags == (ReviewFlag.CRITICAL_GAP,) and critical.needs_counsel
    assert (critical.meets, critical.deviates, critical.missing) == (2, 1, 0)

    minor = summarise(
        approved,
        [
            finding(LIABILITY, Finding.MEETS),
            finding(TERMINATION, Finding.MISSING),
            finding(GOVERNING_LAW, Finding.MISSING),
        ],
    )
    assert minor.flags == (ReviewFlag.MAJOR_GAP,) and not minor.needs_counsel


def test_a_low_confidence_finding_sends_the_review_to_counsel() -> None:
    summary = summarise(
        playbook(PlaybookStatus.APPROVED),
        [
            finding(LIABILITY, Finding.MEETS, confidence=0.3),
            finding(TERMINATION, Finding.MEETS),
            finding(GOVERNING_LAW, Finding.MEETS),
        ],
    )
    assert summary.flags == (ReviewFlag.LOW_CONFIDENCE,) and summary.needs_counsel


def test_summary_refuses_an_unapproved_playbook_or_an_unreviewed_position() -> None:
    with pytest.raises(InvariantViolationError):
        summarise(playbook(), [])
    with pytest.raises(InvariantViolationError):
        summarise(playbook(PlaybookStatus.APPROVED), [finding(LIABILITY, Finding.MEETS)])


async def test_the_offline_judge_reads_words_not_numbers() -> None:
    """The baseline the model is measured against: right on presence, blind to detail."""
    judge = OfflineClauseJudge()
    meets = await judge.judge(
        LIABILITY,
        bundle(
            span(
                "C1",
                "Limitation of liability. Aggregate liability is capped at 12 months' fees; "
                "confidentiality breaches are uncapped.",
            )
        ),
    )
    assert meets.finding is Finding.MEETS and meets.citation_labels == ("C1",)

    blind = await judge.judge(
        LIABILITY,
        bundle(
            span(
                "C1",
                "Limitation of liability. Aggregate liability is capped at 36 months' fees; "
                "confidentiality breaches are uncapped.",
            )
        ),
    )
    assert blind.finding is Finding.MEETS  # 36 is not 12, and the baseline cannot tell

    missing = await judge.judge(TERMINATION, bundle(span("C1", "Payment is due within 30 days.")))
    assert missing.finding is Finding.MISSING and missing.citation_labels == ()
