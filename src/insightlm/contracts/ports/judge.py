"""Judging one position against one bundle of evidence."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from insightlm.contracts.domain.playbook import Position
from insightlm.contracts.domain.review import Finding
from insightlm.retrieval.domain.evidence import EvidenceBundle


@dataclass(frozen=True, slots=True)
class ClauseJudgment:
    finding: Finding
    confidence: float
    rationale: str
    citation_labels: tuple[str, ...]
    model_version: str
    prompt_version: str
    input_tokens: int = 0
    output_tokens: int = 0


class ClauseJudge(Protocol):
    async def judge(self, position: Position, evidence: EvidenceBundle) -> ClauseJudgment:
        """MISSING when no passage addresses the clause; DEVIATES when one does but
        departs from the standard. A MEETS or DEVIATES finding must cite."""
        ...
