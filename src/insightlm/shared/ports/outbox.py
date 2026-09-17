"""Transactional outbox.

Events are appended in the *same transaction* as the domain write, then relayed to
the queue by a separate poller. Durability therefore never depends on the broker --
which is what makes ARQ-on-Redis a safe choice (see ADR-0014).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from insightlm.shared.domain.ids import CorrelationId, OutboxEventId, TenantId


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    id: OutboxEventId
    tenant_id: TenantId
    event_type: str
    payload: dict[str, object]
    correlation_id: CorrelationId | None = None
    occurred_at: datetime | None = None
    attributes: dict[str, str] = field(default_factory=dict)


class Outbox(Protocol):
    def add(self, event: OutboxEvent) -> None:
        """Stage an event. It becomes durable when the unit of work commits."""
        ...
