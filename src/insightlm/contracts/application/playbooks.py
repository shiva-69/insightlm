"""Drafting and approving playbooks."""

from __future__ import annotations

from dataclasses import dataclass

from insightlm.contracts.domain.audit_actions import ContractAudit
from insightlm.contracts.domain.ids import PlaybookId, new_playbook_id, new_position_id
from insightlm.contracts.domain.playbook import Playbook, Position, Severity
from insightlm.contracts.domain.roles import ContractPermission
from insightlm.contracts.ports.repositories import ContractsUnitOfWorkFactory
from insightlm.shared.domain.errors import NotFoundError
from insightlm.shared.domain.policy import Actor, require
from insightlm.shared.domain.roles import PlatformRole
from insightlm.shared.ports.clock import Clock


@dataclass(frozen=True, slots=True)
class PositionDraft:
    clause: str
    standard: str
    severity: Severity
    evidence_hint: str = ""


class ProposePlaybook:
    """A new playbook, or a new version of one with the same name. Always a draft."""

    def __init__(self, uow_factory: ContractsUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, actor: Actor, name: str, positions: list[PositionDraft]) -> Playbook:
        require(actor, ContractPermission.PLAYBOOK_PROPOSE)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            version = await uow.playbooks.latest_version(name.strip()) + 1
            playbook = Playbook(
                id=new_playbook_id(),
                tenant_id=actor.tenant_id,
                name=name.strip(),
                version=version,
                positions=tuple(
                    Position(
                        id=new_position_id(),
                        clause=p.clause.strip(),
                        standard=p.standard.strip(),
                        severity=p.severity,
                        evidence_hint=p.evidence_hint.strip(),
                    )
                    for p in positions
                ),
                proposed_by=actor.user_id,
                created_at=self._clock.now(),
            )
            await uow.playbooks.add(playbook)
            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=ContractAudit.PLAYBOOK_PROPOSED,
                subject_type="playbook",
                subject_id=str(playbook.id),
                payload={"name": playbook.name, "version": version, "positions": len(positions)},
            )
            await uow.commit()
        return playbook


class ApprovePlaybook:
    def __init__(self, uow_factory: ContractsUnitOfWorkFactory, clock: Clock) -> None:
        self._uow_factory = uow_factory
        self._clock = clock

    async def execute(self, actor: Actor, playbook_id: PlaybookId) -> Playbook:
        require(actor, ContractPermission.PLAYBOOK_APPROVE)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            playbook = await uow.playbooks.get(playbook_id)
            if playbook is None:
                raise NotFoundError("playbook not found")
            approved = playbook.approve(
                by=actor.user_id,
                at=self._clock.now(),
                may_self_approve=actor.role == PlatformRole.OWNER,
            )
            previous = await uow.playbooks.approved_named(playbook.name)
            if previous is not None:
                await uow.playbooks.update(previous.superseded())
            await uow.playbooks.update(approved)
            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=ContractAudit.PLAYBOOK_APPROVED,
                subject_type="playbook",
                subject_id=str(playbook.id),
                payload={"name": playbook.name, "version": playbook.version},
            )
            await uow.commit()
        return approved


class ListPlaybooks:
    def __init__(self, uow_factory: ContractsUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: Actor) -> list[Playbook]:
        require(actor, ContractPermission.REVIEW_VIEW)
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            return await uow.playbooks.list_for_tenant(actor.tenant_id)
