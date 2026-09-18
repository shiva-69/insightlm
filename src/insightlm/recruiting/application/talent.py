"""Talent-pool search: the document engine pointed at every candidate at once.

"Who has run Kubernetes in production?" returns *people*, each with the passage that
matched. It's retrieval only, with no generation step, so a search costs at most one
embedding call and nothing is summarised that a recruiter can't read for themselves.
"""

from __future__ import annotations

from dataclasses import dataclass

from insightlm.recruiting.domain.assessment import Candidate
from insightlm.recruiting.ports.evidence import EvidenceProvider
from insightlm.recruiting.ports.repositories import RecruitingUnitOfWorkFactory
from insightlm.retrieval.domain.evidence import EvidenceQuery, RetrievalStrategy, RetrievedSpan
from insightlm.shared.domain.errors import ValidationError
from insightlm.shared.domain.policy import Actor, require
from insightlm.shared.domain.roles import Permission

# Passages retrieved per search. Several can belong to one candidate, so this is wider
# than the number of people shown.
SEARCH_DEPTH = 30
PASSAGES_PER_CANDIDATE = 2


@dataclass(frozen=True, slots=True)
class TalentMatch:
    candidate: Candidate
    passages: tuple[RetrievedSpan, ...]

    @property
    def best_score(self) -> float:
        return self.passages[0].score


class SearchTalentPool:
    def __init__(
        self,
        uow_factory: RecruitingUnitOfWorkFactory,
        evidence: EvidenceProvider,
        *,
        min_relative_score: float = 0.0,
    ) -> None:
        """``min_relative_score`` drops people whose best passage scores below this
        fraction of the top match. It is calibrated per embedder (docs/07, Finding 20):
        0.9 with gemini-embedding-001 lifts F1 from 0.74 to 0.92, while the same value on
        the offline embedder would cut recall to almost nothing, so there it is 0."""
        self._uow_factory = uow_factory
        self._evidence = evidence
        self._min_relative_score = min_relative_score

    async def execute(self, actor: Actor, query: str, *, limit: int = 10) -> list[TalentMatch]:
        # Search crosses requisitions, so it's for people who may see across them.
        require(actor, Permission.SCORE_VIEW)
        if not query.strip():
            raise ValidationError("say what you're looking for")

        async with self._evidence.for_actor(actor) as port:
            bundle = await port.find(
                EvidenceQuery(
                    text=query.strip(),
                    top_k=SEARCH_DEPTH,
                    strategy=RetrievalStrategy.DENSE,
                )
            )
        if bundle.is_empty:
            return []

        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            candidates = await uow.candidates.with_any_document(
                list(dict.fromkeys(s.document_id for s in bundle.spans))
            )

        # A document can belong to more than one candidate record (the same CV attached
        # twice). Each candidate gets the passages from their own documents, best first.
        matches: list[TalentMatch] = []
        for candidate in candidates:
            own = set(candidate.document_ids)
            passages = tuple(s for s in bundle.spans if s.document_id in own)
            if passages:
                matches.append(TalentMatch(candidate, passages[:PASSAGES_PER_CANDIDATE]))
        matches.sort(key=lambda m: -m.best_score)
        if matches and self._min_relative_score > 0:
            floor = matches[0].best_score * self._min_relative_score
            matches = [m for m in matches if m.best_score >= floor]
        return matches[:limit]
