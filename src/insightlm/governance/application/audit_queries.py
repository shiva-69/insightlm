"""Reading and verifying the audit log.

Both operations require ``AUDIT_READ``, which only Owner and Auditor hold. An auditor
can read everything here and write nothing anywhere -- that asymmetry is the role's
entire purpose.
"""

from __future__ import annotations

from insightlm.governance.domain.audit import AuditEntry, ChainVerification
from insightlm.governance.ports.uow import GovernanceUnitOfWorkFactory
from insightlm.shared.domain.policy import Actor, require
from insightlm.shared.domain.roles import PlatformPermission


class ListAuditEntries:
    def __init__(self, uow_factory: GovernanceUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: Actor, *, limit: int = 100, offset: int = 0) -> list[AuditEntry]:
        require(actor, PlatformPermission.AUDIT_READ)
        async with self._uow_factory(tenant_id=actor.tenant_id) as uow:
            return await uow.audit.list_for_tenant(
                actor.tenant_id, limit=min(limit, 500), offset=offset
            )


class VerifyAuditChain:
    """Walks the tenant's chain and reports whether it is intact.

    This is the operation that makes tamper-evidence demonstrable: mutate a row
    directly in psql, call this, and it names the entry where the chain breaks.
    """

    def __init__(self, uow_factory: GovernanceUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: Actor) -> ChainVerification:
        require(actor, PlatformPermission.AUDIT_READ)
        async with self._uow_factory(tenant_id=actor.tenant_id) as uow:
            return await uow.audit.verify_chain(actor.tenant_id)
