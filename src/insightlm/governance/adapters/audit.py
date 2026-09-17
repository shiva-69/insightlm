"""SQLAlchemy audit log adapter."""

from __future__ import annotations

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from insightlm.governance.adapters.models import AuditEntryRow
from insightlm.governance.domain.audit import (
    GENESIS_HASH,
    AuditAction,
    AuditEntry,
    ChainVerification,
    verify_chain,
)
from insightlm.shared.adapters.db.errors import as_domain_errors
from insightlm.shared.domain.ids import (
    AuditEntryId,
    TenantId,
    UserId,
    new_audit_entry_id,
)
from insightlm.shared.ports.clock import Clock


def _to_entry(row: AuditEntryRow) -> AuditEntry:
    return AuditEntry(
        id=AuditEntryId(row.id),
        tenant_id=TenantId(row.tenant_id),
        actor_id=UserId(row.actor_id) if row.actor_id else None,
        action=AuditAction(row.action),
        subject_type=row.subject_type,
        subject_id=row.subject_id,
        payload=dict(row.payload),
        occurred_at=row.occurred_at,
        prev_hash=row.prev_hash,
        entry_hash=row.entry_hash,
        model_version=row.model_version,
        prompt_version=row.prompt_version,
    )


class SqlAlchemyAuditLog:
    """Appends hash-chained entries within the caller's transaction.

    **Concurrency.** Two transactions appending to the same tenant's chain would both
    read the same head, compute the same ``prev_hash``, and fork the chain. A
    transaction-scoped advisory lock serialises appends per tenant; it is released
    automatically on commit or rollback, so no unlock path can be missed. The
    ``(tenant_id, sequence)`` unique constraint is the backstop if the lock is ever
    bypassed. Locking per tenant rather than globally means one tenant's write volume
    never serialises another's.
    """

    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

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
        # Persist whatever domain writes are pending before appending the entry that
        # describes them. An audit entry has foreign keys to the tenant and actor it
        # references, and SQLAlchemy orders a flush by *mapper* dependency -- which it
        # derives from `relationship()`. This schema deliberately declares none
        # (repositories map rows to domain objects explicitly, and lazy loading has no
        # place behind a repository port), so the ORM has no way to know the audit row
        # must go last. Flushing here makes the ordering explicit instead of lucky.
        #
        # Still one transaction: the caller's unit of work commits both or neither.
        async with as_domain_errors(self._session):
            await self._session.flush()

        await self._session.execute(
            text("SELECT pg_advisory_xact_lock(hashtext(:key))"),
            {"key": f"audit:{tenant_id}"},
        )

        head = (
            await self._session.execute(
                select(AuditEntryRow.sequence, AuditEntryRow.entry_hash)
                .where(AuditEntryRow.tenant_id == tenant_id)
                .order_by(AuditEntryRow.sequence.desc())
                .limit(1)
            )
        ).first()

        next_sequence = (head.sequence + 1) if head else 1
        prev_hash = head.entry_hash if head else GENESIS_HASH

        entry = AuditEntry.link(
            entry_id=new_audit_entry_id(),
            tenant_id=tenant_id,
            actor_id=actor_id,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            payload=payload or {},
            occurred_at=self._clock.now(),
            prev_hash=prev_hash,
            model_version=model_version,
            prompt_version=prompt_version,
        )

        self._session.add(
            AuditEntryRow(
                id=entry.id,
                tenant_id=entry.tenant_id,
                sequence=next_sequence,
                actor_id=entry.actor_id,
                action=entry.action.value,
                subject_type=entry.subject_type,
                subject_id=entry.subject_id,
                payload=dict(entry.payload),
                occurred_at=entry.occurred_at,
                prev_hash=entry.prev_hash,
                entry_hash=entry.entry_hash,
                model_version=entry.model_version,
                prompt_version=entry.prompt_version,
            )
        )
        # Flush now, not at commit. Two entries appended in one transaction must each
        # see the previous one to compute `prev_hash`, and an unflushed row is
        # invisible to the head query above.
        async with as_domain_errors(self._session):
            await self._session.flush()
        return entry

    async def list_for_tenant(
        self, tenant_id: TenantId, *, limit: int = 100, offset: int = 0
    ) -> list[AuditEntry]:
        rows = (
            await self._session.execute(
                select(AuditEntryRow)
                .where(AuditEntryRow.tenant_id == tenant_id)
                .order_by(AuditEntryRow.sequence.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
        return [_to_entry(row) for row in rows]

    async def verify_chain(self, tenant_id: TenantId) -> ChainVerification:
        """Walk the whole chain in sequence order.

        Reads every entry, so it belongs in a background job once a tenant's log is
        large. Phase 0 runs it on demand; Phase 7 schedules it and alerts on a break.
        """
        rows = (
            await self._session.execute(
                select(AuditEntryRow)
                .where(AuditEntryRow.tenant_id == tenant_id)
                .order_by(AuditEntryRow.sequence)
            )
        ).scalars()
        return verify_chain(tenant_id, [_to_entry(row) for row in rows])
