"""Repositories and the unit of work for contract review."""

from __future__ import annotations

from typing import Protocol

from insightlm.contracts.domain.ids import ContractReviewId, PlaybookId
from insightlm.contracts.domain.playbook import Playbook
from insightlm.contracts.domain.review import ContractReview
from insightlm.governance.ports.audit import AuditLog
from insightlm.shared.domain.ids import TenantId, UserId
from insightlm.shared.ports.uow import UnitOfWork


class PlaybookRepository(Protocol):
    async def add(self, playbook: Playbook) -> None: ...

    async def get(self, playbook_id: PlaybookId) -> Playbook | None: ...

    async def update(self, playbook: Playbook) -> None: ...

    async def list_for_tenant(self, tenant_id: TenantId) -> list[Playbook]: ...

    async def approved_named(self, name: str) -> Playbook | None:
        """The approved version of a playbook, by name, if there is one."""
        ...

    async def latest_version(self, name: str) -> int: ...


class ContractReviewRepository(Protocol):
    async def add(self, review: ContractReview) -> None: ...

    async def get(self, review_id: ContractReviewId) -> ContractReview | None: ...

    async def list_for_tenant(self, tenant_id: TenantId) -> list[ContractReview]: ...


class ContractsUnitOfWork(UnitOfWork, Protocol):
    @property
    def playbooks(self) -> PlaybookRepository: ...

    @property
    def reviews(self) -> ContractReviewRepository: ...

    @property
    def audit(self) -> AuditLog: ...


class ContractsUnitOfWorkFactory(Protocol):
    def __call__(
        self, *, tenant_id: TenantId | None = None, user_id: UserId | None = None
    ) -> ContractsUnitOfWork: ...
