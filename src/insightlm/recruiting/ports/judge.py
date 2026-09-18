"""Judging one criterion against one bundle of evidence.

The model is never asked "how good is this candidate?". It is asked a narrow,
evidence-bounded question -- does *this* evidence support *this* criterion? -- and it
answers with a verdict and the source labels behind it. Narrow questions hallucinate
less, are individually testable, parallelise cleanly, and keep the arithmetic in code
where it can be defended (see domain/assessment.aggregate).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from insightlm.recruiting.domain.assessment import Verdict
from insightlm.recruiting.domain.rubric import Criterion
from insightlm.retrieval.domain.evidence import EvidenceBundle


@dataclass(frozen=True, slots=True)
class JudgeResult:
    verdict: Verdict
    confidence: float
    rationale: str
    citation_labels: tuple[str, ...]
    model_version: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0


class CriterionJudge(Protocol):
    async def judge(self, criterion: Criterion, evidence: EvidenceBundle) -> JudgeResult:
        """Return ABSENT when the evidence does not bear on the criterion.

        Absent is a finding about the *evidence*, not the candidate, and it must be
        returned rather than a guess -- the aggregate treats it differently from WEAK.
        """
        ...
