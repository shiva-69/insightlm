"""The transaction boundary -- what replaces Spring's ``@Transactional``.

Rules (see docs/06 SS7), enforced by review and by the one-commit test:

* One unit of work per request or task, opened at the **use-case** boundary.
* ``session.commit()`` appears in exactly one file: the SQLAlchemy UoW adapter.
* Repositories receive a session; they never create or commit one.
* Exiting without calling ``commit()`` rolls back. There is no implicit commit.
* Concurrent fan-out (``asyncio.gather``) must not share a unit of work. Each task
  gets its own, or -- preferably -- does no I/O against the database at all.
"""

from __future__ import annotations

from types import TracebackType
from typing import Protocol, Self

from insightlm.shared.ports.outbox import Outbox


class UnitOfWork(Protocol):
    """A transaction, plus the outbox that shares it.

    Members are declared as read-only properties rather than plain attributes. A
    mutable Protocol attribute is *invariant*, so an adapter exposing a concrete
    ``SqlAlchemyOutbox`` would not satisfy a Protocol demanding ``Outbox``. Read-only
    properties are covariant, which is what we actually mean: callers read these, they
    never reassign them.
    """

    @property
    def outbox(self) -> Outbox: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        """Rolls back unless :meth:`commit` was called."""
        ...

    async def commit(self) -> None: ...

    async def rollback(self) -> None: ...
