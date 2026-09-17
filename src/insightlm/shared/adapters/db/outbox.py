"""SQLAlchemy outbox adapter."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from insightlm.shared.adapters.db.models import OutboxEventRow
from insightlm.shared.ports.outbox import OutboxEvent


class SqlAlchemyOutbox:
    """Stages events on the session, so they are inserted by the same flush -- and
    therefore the same transaction -- as the domain write they describe.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    def add(self, event: OutboxEvent) -> None:
        self._session.add(
            OutboxEventRow(
                id=event.id,
                tenant_id=event.tenant_id,
                event_type=event.event_type,
                payload=dict(event.payload),
                attributes=dict(event.attributes),
                correlation_id=event.correlation_id,
                occurred_at=event.occurred_at or datetime.now(UTC),
            )
        )
