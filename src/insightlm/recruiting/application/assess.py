"""Candidates, assessment, and the shortlist.

`RunAssessment` is where the thesis is exercised end to end:

1. It refuses outright when the requisition has no approved rubric.
2. It asks retrieval -- through the port, and nothing else -- for evidence per
   criterion, scoped to this candidate's documents only.
3. It asks a judge one narrow question per criterion.
4. It resolves every citation against the evidence actually retrieved, and downgrades
   any finding that cites nothing real. A judge cannot smuggle an unsupported verdict
   into an assessment, whichever provider it is.
5. It does the arithmetic itself, in code.

Concurrency follows the Phase 0 rule: the evidence port holds one database session,
so retrieval runs sequentially inside it. Only the judging -- pure provider I/O, no
database -- fans out, under a semaphore so a large rubric cannot open fifty
simultaneous calls to a rate-limited provider.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace

from insightlm.governance.domain.redaction import NO_REDACTION, RedactionProfile, redact
from insightlm.recruiting.domain.assessment import (
    Application,
    Assessment,
    Candidate,
    CriterionJudgment,
    EvidenceRef,
    Stage,
    Verdict,
    aggregate,
)
from insightlm.recruiting.domain.assessment_job import AssessmentJob
from insightlm.recruiting.domain.audit_actions import RecruitingAudit
from insightlm.recruiting.domain.roles import RecruitingPermission
from insightlm.recruiting.domain.rubric import Criterion
from insightlm.recruiting.domain.screening import BLIND
from insightlm.recruiting.ports.evidence import EvidenceProvider
from insightlm.recruiting.ports.judge import CriterionJudge, JudgeResult
from insightlm.recruiting.ports.repositories import RecruitingUnitOfWorkFactory
from insightlm.retrieval.domain.evidence import EvidenceBundle, EvidenceQuery, RetrievalStrategy
from insightlm.shared.domain.errors import ConflictError, NotFoundError
from insightlm.shared.domain.ids import (
    ApplicationId,
    DocumentId,
    RequisitionId,
    new_application_id,
    new_assessment_id,
    new_candidate_id,
)
from insightlm.shared.domain.policy import Actor, require
from insightlm.shared.observability.logging import get_logger
from insightlm.shared.ports.clock import Clock
from insightlm.shared.ports.limits import ModelBudget

_logger = get_logger(__name__)

EVIDENCE_PER_CRITERION = 5
MAX_CONCURRENT_JUDGEMENTS = 4


class AddCandidate:
    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(
        self,
        actor: Actor,
        requisition_id: RequisitionId,
        *,
        name: str,
        email: str | None,
        document_ids: list[DocumentId],
    ) -> tuple[Candidate, Application]:
        require(actor, RecruitingPermission.REQUISITION_MANAGE)
        now = self._clock.now()
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            if await uow.requisitions.get(requisition_id) is None:
                raise NotFoundError("requisition not found")
            candidate = Candidate(
                id=new_candidate_id(),
                tenant_id=actor.tenant_id,
                name=name.strip(),
                email=email.strip().lower() if email else None,
                document_ids=tuple(dict.fromkeys(document_ids)),
                created_at=now,
            )
            application = Application(
                id=new_application_id(),
                tenant_id=actor.tenant_id,
                candidate_id=candidate.id,
                requisition_id=requisition_id,
                stage=Stage.APPLIED,
                created_at=now,
                stage_entered_at=now,
            )
            await uow.candidates.add(candidate)
            await uow.applications.add(application)
            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=RecruitingAudit.CANDIDATE_ADDED,
                subject_type="application",
                subject_id=str(application.id),
                payload={
                    "requisition_id": str(requisition_id),
                    "documents": len(candidate.document_ids),
                },
            )
            await uow.commit()
        return candidate, application


def _resolve(
    criterion: Criterion, result: JudgeResult, bundle: EvidenceBundle
) -> CriterionJudgment:
    """Bind a judge's labels to real evidence, and refuse what cannot be bound.

    A finding that cites nothing retrievable is downgraded to ABSENT with the reason
    recorded, rather than kept. The alternative -- keeping a verdict whose evidence
    does not exist -- is precisely the unexplainable score this system exists to
    prevent.
    """
    spans = bundle.by_label()
    evidence = tuple(
        EvidenceRef(
            label=span.label,
            chunk_id=str(span.chunk_id),
            document_id=str(span.document_id),
            page=span.page,
            char_start=span.span.char_start,
            char_end=span.span.char_end,
            quoted_text=span.text,
            document_title=span.document_title,
            rects=span.span.rects,
        )
        for label in dict.fromkeys(label.strip().upper() for label in result.citation_labels)
        if (span := spans.get(label)) is not None
    )

    if result.verdict is not Verdict.ABSENT and not evidence:
        _logger.warning("judgment_downgraded_uncited", criterion=criterion.label)
        return CriterionJudgment(
            criterion_id=criterion.id,
            verdict=Verdict.ABSENT,
            confidence=0.0,
            rationale=(
                f"The judge returned '{result.verdict.value}' but cited no passage that was "
                "retrieved, so the finding was discarded."
            ),
            evidence=(),
        )
    if result.verdict is Verdict.ABSENT:
        evidence = ()

    return CriterionJudgment(
        criterion_id=criterion.id,
        verdict=result.verdict,
        confidence=max(0.0, min(1.0, result.confidence)),
        rationale=result.rationale.strip(),
        evidence=evidence,
    )


def evidence_as_seen_by_judge(
    bundle: EvidenceBundle, profile: RedactionProfile, candidate: Candidate
) -> EvidenceBundle:
    """Redact every field of a passage the model sees -- not just its text.

    The title is often the person's name ("Asha Rao CV"), and so is the first
    "section": the chunker reads the name at the top of a CV as a heading. Redacting
    only the text leaked the name through the section attribute; the integration test
    caught it.
    """
    if profile.is_identity:
        return bundle

    def clean(value: str) -> str:
        return redact(value, profile, person_names=[candidate.name]).text

    return replace(
        bundle,
        spans=tuple(
            replace(
                span,
                text=clean(span.text),
                document_title=clean(span.document_title),
                section_label=clean(span.section_label),
            )
            for span in bundle.spans
        ),
    )


class RunAssessment:
    def __init__(
        self,
        uow_factory: RecruitingUnitOfWorkFactory,
        evidence: EvidenceProvider,
        judge: CriterionJudge,
        clock: Clock,
        budget: ModelBudget,
    ) -> None:
        # Required, not defaulted: whether model calls are metered is a deployment
        # decision, made in the composition root, never by the use case.
        self._uow_factory = uow_factory
        self._evidence = evidence
        self._judge = judge
        self._clock = clock
        self._budget = budget

    async def execute(self, actor: Actor, application_id: ApplicationId) -> Assessment:
        require(actor, RecruitingPermission.ASSESSMENT_RUN)

        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            application = await uow.applications.get(application_id)
            if application is None:
                raise NotFoundError("application not found")
            candidate = await uow.candidates.get(application.candidate_id)
            if candidate is None:
                raise NotFoundError("candidate not found")
            rubric = await uow.rubrics.approved_for(application.requisition_id)
            requisition = await uow.requisitions.get(application.requisition_id)

        if rubric is None:
            # The rule the project turns on. There is no path around this.
            raise ConflictError(
                "this requisition has no approved rubric; a human must approve the "
                "criteria before anyone is assessed against them"
            )

        bundles: dict[object, EvidenceBundle] = {}
        async with self._evidence.for_actor(actor) as port:
            for criterion in rubric.criteria:
                bundles[criterion.id] = await port.find(
                    EvidenceQuery(
                        text=criterion.query,
                        top_k=EVIDENCE_PER_CRITERION,
                        document_ids=candidate.document_ids,
                        strategy=RetrievalStrategy.DENSE,
                    )
                )

        # One paid call per criterion that has evidence; judges skip the rest for free.
        # Reserved before any is made, so a workspace over budget spends nothing.
        await self._budget.charge(
            actor.tenant_id, sum(1 for b in bundles.values() if not b.is_empty)
        )

        profile = BLIND if requisition is None or requisition.blind_screening else NO_REDACTION
        gate = asyncio.Semaphore(MAX_CONCURRENT_JUDGEMENTS)

        async def judged(criterion: Criterion) -> JudgeResult:
            # The judge reads the redacted copy; `_resolve` below quotes the original.
            # Labels are shared, so a citation made blind still points at real text.
            shown = evidence_as_seen_by_judge(bundles[criterion.id], profile, candidate)
            async with gate:
                return await self._judge.judge(criterion, shown)

        results = await asyncio.gather(*(judged(c) for c in rubric.criteria))
        judgments = [
            _resolve(criterion, result, bundles[criterion.id])
            for criterion, result in zip(rubric.criteria, results, strict=True)
        ]
        score, flags = aggregate(rubric, judgments)

        first = results[0]
        assessment = Assessment(
            id=new_assessment_id(),
            tenant_id=actor.tenant_id,
            application_id=application.id,
            rubric_id=rubric.id,
            rubric_version=rubric.version,
            judgments=tuple(judgments),
            score=score,
            flags=flags,
            assessed_by=actor.user_id,
            created_at=self._clock.now(),
            model_version=first.model_version,
            prompt_version=first.prompt_version,
            retrieval_config_hash=next(iter(bundles.values())).config_hash,
            redaction_profile=profile.id,
        )

        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            await uow.assessments.add(assessment)
            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=RecruitingAudit.ASSESSMENT_CREATED,
                subject_type="assessment",
                subject_id=str(assessment.id),
                payload={
                    "application_id": str(application.id),
                    "score": score,
                    "flags": [f.value for f in flags],
                    "rubric_version": rubric.version,
                },
                model_version=assessment.model_version,
                prompt_version=assessment.prompt_version,
            )
            await uow.commit()

        return assessment


@dataclass(frozen=True, slots=True)
class ShortlistEntry:
    application: Application
    candidate: Candidate
    assessment: Assessment | None
    job: AssessmentJob | None = None
    """The application's most recent assessment job, so progress can be shown."""
    stale: bool = False
    """Assessed under an older rubric than the one now in force."""


class Shortlist:
    """Applications ranked by score, unassessed last.

    The ranking is only ever an ordering for a human to work through. Every entry
    carries its flags and its evidence, and the UI shows the breakdown before the
    number, because a score with the evidence one click away invites rubber-stamping.
    """

    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: Actor, requisition_id: RequisitionId) -> list[ShortlistEntry]:
        require(actor, RecruitingPermission.SCORE_VIEW)
        entries: list[ShortlistEntry] = []
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            applications = await uow.applications.list_for_requisition(requisition_id)
            rubric = await uow.rubrics.approved_for(requisition_id)
            jobs = await uow.assessment_jobs.latest_for_applications([a.id for a in applications])
            for application in applications:
                candidate = await uow.candidates.get(application.candidate_id)
                if candidate is None:
                    continue
                assessment = await uow.assessments.latest_for(application.id)
                entries.append(
                    ShortlistEntry(
                        application=application,
                        candidate=candidate,
                        assessment=assessment,
                        job=jobs.get(application.id),
                        stale=bool(
                            assessment and rubric and assessment.rubric_version < rubric.version
                        ),
                    )
                )
        entries.sort(
            key=lambda e: (e.assessment is None, -(e.assessment.score if e.assessment else 0))
        )
        return entries
