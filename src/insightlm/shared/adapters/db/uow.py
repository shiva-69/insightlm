"""The SQLAlchemy unit of work -- the only place ``commit()`` is called.

If you find yourself wanting to commit somewhere else, what you actually want is a
second unit of work. See docs/06 SS7.
"""

from __future__ import annotations

from types import TracebackType
from typing import Self

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.shared.adapters.db.base import set_actor_context, set_tenant_context
from insightlm.shared.adapters.db.errors import as_domain_errors
from insightlm.shared.adapters.db.outbox import SqlAlchemyOutbox
from insightlm.shared.domain.errors import InvariantViolationError
from insightlm.shared.domain.ids import TenantId, UserId


class SqlAlchemyUnitOfWork:
    """Opens a transaction, scopes it to a tenant, and rolls back unless committed.

    ``tenant_id`` is optional because a few flows legitimately have no tenant yet --
    registration, login, and invitation acceptance before the token is parsed. Those
    flows touch only tables that carry no tenant data. Everything else runs with the
    context set, and the RLS policies deny access when it is missing.
    """

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        tenant_id: TenantId | None = None,
        user_id: UserId | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._tenant_id = tenant_id
        self._user_id = user_id
        self._session: AsyncSession | None = None
        self._committed = False
        self._finished = False

    @property
    def session(self) -> AsyncSession:
        if self._session is None:
            raise InvariantViolationError("unit of work used outside its context manager")
        return self._session

    @property
    def tenant_id(self) -> TenantId | None:
        return self._tenant_id

    async def __aenter__(self) -> Self:
        if self._session is not None:
            raise InvariantViolationError("unit of work is not re-entrant")
        self._session = self._session_factory()
        await self._session.begin()
        if self._tenant_id is not None:
            await set_tenant_context(self._session, self._tenant_id)
        if self._user_id is not None:
            await set_actor_context(self._session, self._user_id)
        self.outbox = SqlAlchemyOutbox(self._session)
        self._build_repositories(self._session)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        try:
            if not self._committed:
                await self.session.rollback()
        finally:
            self._finished = True
            await self.session.close()

    async def commit(self) -> None:
        """The only commit in the codebase.

        Integrity violations are translated here, once, into a domain error. Under
        row-level security a uniqueness *check* by SELECT is unreliable -- the
        conflicting row may be invisible to the current tenant -- so the constraint is
        the only honest arbiter, and this is where its verdict is read.
        """
        if self._committed:
            raise InvariantViolationError("unit of work has already been committed")
        async with as_domain_errors(self.session):
            await self.session.flush()
            await self.session.commit()
        self._committed = True

    async def rollback(self) -> None:
        await self.session.rollback()

    def _build_repositories(self, session: AsyncSession) -> None:
        """Subclasses attach their repositories here, all sharing one session."""
