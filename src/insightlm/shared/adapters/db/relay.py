"""Outbox relay: drains staged domain events onto the queue.

**Why this needs a declared exemption.** Every tenant-scoped table is protected by a
row-level security policy keyed on ``app.tenant_id``. The relay is by definition
cross-tenant -- it drains whatever is pending -- so it cannot satisfy those policies.
It therefore sets ``app.relay = 'on'`` for its transaction, and the outbox policy
accepts that as an alternative to a tenant match.

It is worth being precise about what this does and does not buy. RLS here defends
against a *forgotten* ``WHERE`` clause, which is the failure that actually happens. It
does not defend against application code that deliberately lies, because the
application owns the connection either way. The stronger version -- a separate
database role holding ``BYPASSRLS`` with grants limited to this one table -- is the
right move once the relay runs as its own deployment, and is recorded as the migration
path in ADR-0016.
"""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.shared.adapters.db.models import OutboxEventRow
from insightlm.shared.observability.logging import get_logger

_logger = get_logger(__name__)


class OutboxRelay:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], *, batch_size: int = 100
    ) -> None:
        self._session_factory = session_factory
        self._batch_size = batch_size

    async def drain(self) -> int:
        """Claim and publish one batch. Returns the number published.

        ``FOR UPDATE SKIP LOCKED`` lets several relay instances run concurrently
        without handing the same event to two of them, and without either blocking.
        """
        published = 0
        async with self._session_factory() as session:
            await session.begin()
            try:
                await session.execute(text("SELECT set_config('app.relay', 'on', true)"))

                rows = (
                    await session.execute(
                        select(OutboxEventRow)
                        .where(OutboxEventRow.processed_at.is_(None))
                        .order_by(OutboxEventRow.occurred_at)
                        .limit(self._batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).scalars()

                for row in rows:
                    # Phase 1 enqueues the matching ARQ job here. Phase 0 records the
                    # publication so the relay, its ordering and its observability are
                    # proven before anything depends on them.
                    _logger.info(
                        "outbox_event_published",
                        event_type=row.event_type,
                        event_id=str(row.id),
                        tenant_id=str(row.tenant_id),
                    )
                    row.processed_at = datetime.now(UTC)
                    row.attempts += 1
                    published += 1

                await session.commit()
            except Exception:
                await session.rollback()
                raise
        return published
