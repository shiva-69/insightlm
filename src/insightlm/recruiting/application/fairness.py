"""Recording self-identification, and reporting selection rates by group."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass

from insightlm.recruiting.domain.assessment import Stage
from insightlm.recruiting.domain.fairness import (
    UNDISCLOSED,
    Dimension,
    GroupRate,
    Outcome,
    SelfIdentification,
    selection_rates,
)
from insightlm.recruiting.ports.repositories import RecruitingUnitOfWorkFactory
from insightlm.shared.domain.errors import NotFoundError
from insightlm.shared.domain.ids import CandidateId, RequisitionId
from insightlm.shared.domain.policy import Actor, require
from insightlm.shared.domain.roles import PlatformPermission
from insightlm.shared.ports.clock import Clock

_PAST_SCREENING = frozenset({Stage.SCREENED, Stage.INTERVIEW, Stage.OFFER, Stage.HIRED})


class RecordSelfIdentification:
    """In production this is the candidate's own optional form; here the owner enters it
    on their behalf. Either way it is never visible to anyone assessing them."""

    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(
        self, actor: Actor, candidate_id: CandidateId, dimension: Dimension, value: str
    ) -> None:
        require(actor, PlatformPermission.TENANT_MANAGE)
        record = SelfIdentification(
            tenant_id=actor.tenant_id,
            candidate_id=candidate_id,
            dimension=dimension,
            value=value,
            recorded_at=self._clock.now(),
        )
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            if await uow.candidates.get(candidate_id) is None:
                raise NotFoundError("candidate not found")
            await uow.self_identification.put(record)
            # Deliberately not audited with its value: the audit log is permanent and
            # this is special-category data. That it was recorded is not needed either.
            await uow.commit()


@dataclass(frozen=True, slots=True)
class SelectionReport:
    dimension: Dimension
    stage: str
    groups: list[GroupRate]
    pending: int
    rejection_reasons: dict[str, dict[str, int]]  # group -> reason -> count


class SelectionRates:
    def __init__(self, uow_factory: RecruitingUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(
        self,
        actor: Actor,
        *,
        dimension: Dimension = Dimension.GENDER,
        requisition_id: RequisitionId | None = None,
    ) -> SelectionReport:
        require(actor, PlatformPermission.AUDIT_READ)
        outcomes: list[Outcome] = []
        reasons: dict[str, Counter[str]] = defaultdict(Counter)
        pending = 0
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            requisitions = (
                [requisition_id]
                if requisition_id
                else [r.id for r in await uow.requisitions.list_for_tenant(actor.tenant_id)]
            )
            applications = [
                a for r in requisitions for a in await uow.applications.list_for_requisition(r)
            ]
            identities = await uow.self_identification.for_candidates(
                list({a.candidate_id for a in applications}), dimension
            )
            for application in applications:
                identity = identities.get(application.candidate_id)
                group = identity.group if identity else UNDISCLOSED
                if application.stage in _PAST_SCREENING:
                    outcomes.append(Outcome(group, advanced=True))
                elif application.stage is Stage.REJECTED:
                    history = await uow.transitions.list_for_application(application.id)
                    last = history[-1] if history else None
                    # Rejected from a later stage still got past screening.
                    advanced = last is not None and last.from_stage is not Stage.APPLIED
                    outcomes.append(Outcome(group, advanced=advanced))
                    if last and last.reason:
                        reasons[group][last.reason.value] += 1
                else:
                    pending += 1
        return SelectionReport(
            dimension=dimension,
            stage="screening",
            groups=selection_rates(outcomes),
            pending=pending,
            rejection_reasons={g: dict(c) for g, c in reasons.items()},
        )
