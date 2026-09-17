"""Governance's unit of work.

Narrower than identity's on purpose: reading and verifying the audit log needs the
audit log and nothing else. A port that exposes more than its callers need is a
dependency waiting to be taken.
"""

from __future__ import annotations

from typing import Protocol

from insightlm.governance.ports.audit import AuditLog
from insightlm.shared.domain.ids import TenantId, UserId
from insightlm.shared.ports.uow import UnitOfWork


class GovernanceUnitOfWork(UnitOfWork, Protocol):
    @property
    def audit(self) -> AuditLog: ...


class GovernanceUnitOfWorkFactory(Protocol):
    def __call__(
        self, *, tenant_id: TenantId | None = None, user_id: UserId | None = None
    ) -> GovernanceUnitOfWork: ...
