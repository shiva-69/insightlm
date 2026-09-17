"""The real clock."""

from __future__ import annotations

from datetime import UTC, datetime


class SystemClock:
    """Always timezone-aware UTC -- ruff's DTZ rules keep naive datetimes out."""

    def now(self) -> datetime:
        return datetime.now(UTC)
