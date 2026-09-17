"""Audit log port.

Deliberately offers no update or delete. The absence is the design.
"""

from __future__ import annotations

from typing import Protocol

from insightlm.governance.domain.audit import AuditAction, AuditEntry, ChainVerification
from insightlm.shared.domain.ids import TenantId, UserId


class AuditLog(Protocol):
    async def record(
        self,
        *,
        tenant_id: TenantId,
        actor_id: UserId | None,
        action: AuditAction,
        subject_type: str,
        subject_id: str | None = None,
        payload: dict[str, object] | None = None,
        model_version: str | None = None,
        prompt_version: str | None = None,
    ) -> AuditEntry:
        """Append an entry, chained onto the tenant's current head.

        Must be called inside a unit of work: the entry becomes durable with the
        domain write it describes, never separately.
        """
        ...

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 100, offset: int = 0
    ) -> list[AuditEntry]: ...

    async def verify_chain(self, tenant_id: TenantId) -> ChainVerification: ...
