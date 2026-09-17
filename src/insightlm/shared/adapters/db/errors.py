"""Translating database errors into domain errors.

Constraint violations surface at whichever ``flush()`` happens to hit the database,
which is not always ``commit()``. Keeping the translation in one helper -- rather than
one ``try`` block -- means every flush site reports the same domain error.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from insightlm.shared.domain.errors import ConflictError


def constraint_name(exc: IntegrityError) -> str:
    """Best-effort constraint name, for a useful error that leaks no row values."""
    name = getattr(getattr(exc.orig, "__cause__", None), "constraint_name", None)
    return str(name) if name else "unknown"


@asynccontextmanager
async def as_domain_errors(session: AsyncSession) -> AsyncIterator[None]:
    """Turn an ``IntegrityError`` into a :class:`ConflictError`.

    Under row-level security a uniqueness *check* by SELECT is unreliable -- the
    conflicting row may be invisible to the current tenant -- so the constraint is the
    only honest arbiter, and this is where its verdict is read.

    The transaction is rolled back first: once Postgres aborts it, every subsequent
    statement fails anyway, and leaving it poisoned turns one clear error into a
    confusing second one.
    """
    try:
        yield
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "the write conflicts with existing data",
            detail={"constraint": constraint_name(exc)},
        ) from exc
