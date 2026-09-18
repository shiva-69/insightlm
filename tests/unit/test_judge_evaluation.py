"""The judge benchmark's metrics, and the judge contract it exposed."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from insightlm.evaluation.adapters.judge_cases import JUDGE_V1
from insightlm.evaluation.domain.judging import CaseKind, JudgeCase, JudgeOutcome, JudgeReport
from insightlm.recruiting.adapters.judges import GeminiCriterionJudge, OfflineCriterionJudge
from insightlm.recruiting.domain.assessment import Verdict
from insightlm.shared.domain.errors import UpstreamError
from tests.unit.test_citation_contract import bundle, span
from tests.unit.test_recruiting_domain import criterion

POSITIVE = JudgeCase(
    "x", CaseKind.DIRECT, "Python", ("p",), frozenset({Verdict.STRONG}), frozenset({"C1"})
)
EITHER = JudgeCase("y", CaseKind.MENTION, "Go", ("p",), frozenset({Verdict.ABSENT, Verdict.WEAK}))


def test_a_right_verdict_citing_the_wrong_passage_is_a_citation_miss() -> None:
    outcome = JudgeOutcome(POSITIVE, Verdict.STRONG, ("C2",))
    assert outcome.correct
    assert outcome.citations_correct is False


def test_ambiguous_cases_are_left_out_of_detection_not_counted_wrong() -> None:
    report = JudgeReport("j", [JudgeOutcome(EITHER, Verdict.WEAK, ("C1",))])
    assert report.accuracy == 1.0
    assert report.detection is None


def test_absent_verdicts_are_not_graded_on_citations() -> None:
    report = JudgeReport("j", [JudgeOutcome(EITHER, Verdict.ABSENT, ())])
    assert report.citation_precision is None


def test_the_case_set_covers_every_failure_mode_and_labels_cite_real_passages() -> None:
    assert {c.kind for c in JUDGE_V1} == set(CaseKind)
    assert len({c.id for c in JUDGE_V1}) == len(JUDGE_V1)
    for case in JUDGE_V1:
        labels = {f"C{i + 1}" for i in range(len(case.passages))}
        assert case.supporting <= labels, case.id


async def test_the_offline_baseline_is_measured_where_it_is_weak() -> None:
    """Pins the baseline's profile: perfect on direct wording, blind to negation."""
    report = JudgeReport("offline")
    for case in JUDGE_V1:
        result = await OfflineCriterionJudge().judge(
            criterion(case.criterion),
            bundle(*(span(f"C{i + 1}", text) for i, text in enumerate(case.passages))),
        )
        report.outcomes.append(JudgeOutcome(case, result.verdict, result.citation_labels))
    by_kind = report.by_kind()
    assert by_kind[CaseKind.DIRECT] == 1.0
    assert by_kind[CaseKind.NEGATION] == 0.0


class _FakeModels:
    def __init__(self, parsed: Any) -> None:
        self._parsed = parsed

    async def generate_content(self, **_: Any) -> Any:
        return SimpleNamespace(parsed=self._parsed, usage_metadata=None, candidates=[])


def _gemini(parsed: Any) -> GeminiCriterionJudge:
    judge = GeminiCriterionJudge.__new__(GeminiCriterionJudge)
    judge._client = SimpleNamespace(aio=SimpleNamespace(models=_FakeModels(parsed)))  # type: ignore[attr-defined]
    judge._model = "gemini-test"  # type: ignore[attr-defined]
    from insightlm.recruiting.adapters.judges import PROMPT_DIR, PROMPT_NAME
    from insightlm.shared.adapters.prompts import load_prompt_from

    judge._prompt = load_prompt_from(PROMPT_DIR, PROMPT_NAME)  # type: ignore[attr-defined]
    return judge


@pytest.mark.parametrize(
    "parsed",
    [
        None,  # nothing parsable came back
        {"verdict": "strong", "confidence": 5, "rationale": "x", "citations": ["C1"]},  # seen live
        {"verdict": "excellent", "confidence": 0.9, "rationale": "x", "citations": []},
    ],
)
async def test_a_judge_that_cannot_answer_raises_instead_of_saying_absent(parsed: Any) -> None:
    """Found by the first live run: a malformed verdict used to crash or become "absent"."""
    with pytest.raises(UpstreamError):
        await _gemini(parsed).judge(criterion("Python"), bundle(span("C1", "Python services.")))
