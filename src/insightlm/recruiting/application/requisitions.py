"""Requisitions and rubrics: drafting, proposing, and the human approval gate."""

from __future__ import annotations

from dataclasses import dataclass

from insightlm.recruiting.application.pipeline import is_assignment_scoped
from insightlm.recruiting.domain.audit_actions import RecruitingAudit
from insightlm.recruiting.domain.roles import RecruitingPermission
from insightlm.recruiting.domain.rubric import (
    Criterion,
    CriterionKind,
    Requisition,
    Rubric,
    RubricStatus,
)
from insightlm.recruiting.ports.repositories import RecruitingUnitOfWorkFactory
from insightlm.shared.domain.errors import NotFoundError
from insightlm.shared.domain.ids import (
    RequisitionId,
    RubricId,
    new_criterion_id,
    new_requisition_id,
    new_rubric_id,
)
from insightlm.shared.domain.policy import Actor, require
from insightlm.shared.domain.roles import PlatformRole
from insightlm.shared.ports.clock import Clock


@dataclass(frozen=True, slots=True)
class CriterionDraft:
    label: str
    kind: CriterionKind
    weight: float
    evidence_hint: str = ""
    disqualifying: bool = False


class CreateRequisition:
    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(
        self, actor: Actor, *, title: str, description: str, blind_screening: bool = True
    ) -> Requisition:
        require(actor, RecruitingPermission.REQUISITION_MANAGE)
        requisition = Requisition(
            id=new_requisition_id(),
            tenant_id=actor.tenant_id,
            title=title.strip(),
            description=description.strip(),
            blind_screening=blind_screening,
            created_by=actor.user_id,
            created_at=self._clock.now(),
        )
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            await uow.requisitions.add(requisition)
            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=RecruitingAudit.REQUISITION_CREATED,
                subject_type="requisition",
                subject_id=str(requisition.id),
                payload={"title": requisition.title, "blind_screening": blind_screening},
            )
            await uow.commit()
        return requisition


class ProposeRubric:
    """Anyone with RUBRIC_PROPOSE may draft criteria. Nobody may use them yet."""

    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(
        self, actor: Actor, requisition_id: RequisitionId, criteria: list[CriterionDraft]
    ) -> Rubric:
        require(actor, RecruitingPermission.RUBRIC_PROPOSE)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            if await uow.requisitions.get(requisition_id) is None:
                raise NotFoundError("requisition not found")
            rubric = Rubric(
                id=new_rubric_id(),
                tenant_id=actor.tenant_id,
                requisition_id=requisition_id,
                version=await uow.rubrics.latest_version(requisition_id) + 1,
                criteria=tuple(
                    Criterion(
                        id=new_criterion_id(),
                        label=d.label.strip(),
                        kind=d.kind,
                        weight=d.weight,
                        evidence_hint=d.evidence_hint.strip(),
                        disqualifying=d.disqualifying,
                    )
                    for d in criteria
                ),
                proposed_by=actor.user_id,
                created_at=self._clock.now(),
            )
            await uow.rubrics.add(rubric)
            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=RecruitingAudit.RUBRIC_PROPOSED,
                subject_type="rubric",
                subject_id=str(rubric.id),
                payload={"version": rubric.version, "criteria": [c.label for c in rubric.criteria]},
            )
            await uow.commit()
        return rubric


class ApproveRubric:
    """The gate. Nothing is assessed against criteria a human has not approved."""

    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, actor: Actor, rubric_id: RubricId) -> Rubric:
        require(actor, RecruitingPermission.RUBRIC_APPROVE)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            rubric = await uow.rubrics.get(rubric_id)
            if rubric is None:
                raise NotFoundError("rubric not found")

            approved = rubric.approve(
                by=actor.user_id,
                at=self._clock.now(),
                may_self_approve=actor.role == PlatformRole.OWNER,
            )
            # One rubric in force per requisition. The previous one is superseded, not
            # deleted: assessments already run against it keep pointing at it.
            previous = await uow.rubrics.approved_for(rubric.requisition_id)
            if previous is not None and previous.id != rubric.id:
                await uow.rubrics.update(previous.superseded())
            await uow.rubrics.update(approved)
            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=RecruitingAudit.RUBRIC_APPROVED,
                subject_type="rubric",
                subject_id=str(rubric.id),
                payload={
                    "version": rubric.version,
                    "superseded": str(previous.id) if previous else None,
                    "self_approved": actor.user_id == rubric.proposed_by,
                },
            )
            await uow.commit()
        return approved


@dataclass(frozen=True, slots=True)
class RequisitionDetail:
    requisition: Requisition
    rubrics: list[Rubric]

    @property
    def rubric_in_force(self) -> Rubric | None:
        return next((r for r in self.rubrics if r.status is RubricStatus.APPROVED), None)


class ListRequisitions:
    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: Actor) -> list[Requisition]:
        require(actor, RecruitingPermission.CANDIDATE_VIEW)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            requisitions = await uow.requisitions.list_for_tenant(actor.tenant_id)
            if is_assignment_scoped(actor):
                assigned = await uow.scorecards.requisitions_for_interviewer(actor.user_id)
                requisitions = [r for r in requisitions if r.id in assigned]
            return requisitions


class GetRequisition:
    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: Actor, requisition_id: RequisitionId) -> RequisitionDetail:
        require(actor, RecruitingPermission.CANDIDATE_VIEW)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            requisition = await uow.requisitions.get(requisition_id)
            if requisition is None:
                raise NotFoundError("requisition not found")
            if is_assignment_scoped(actor) and requisition_id not in (
                await uow.scorecards.requisitions_for_interviewer(actor.user_id)
            ):
                raise NotFoundError("requisition not found")
            return RequisitionDetail(
                requisition=requisition,
                rubrics=await uow.rubrics.list_for_requisition(requisition_id),
            )
