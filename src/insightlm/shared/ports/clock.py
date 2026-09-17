"""Time as a dependency.

Domain code never calls ``datetime.now()``. It asks a ``Clock``. That makes hash
chains, token expiry and SLA timers deterministically testable.
"""

from __future__ import annotations

from datetime import datetime
from typing import Protocol


class Clock(Protocol):
    def now(self) -> datetime:
        """Current time, always timezone-aware and in UTC."""
        ...
