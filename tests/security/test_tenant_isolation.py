"""Tenant isolation, proven at the database layer.

The second test here is the one that matters. Anyone can write an application that
filters by tenant. The question is what happens when someone forgets -- and the answer
has to be "nothing leaks", not "we review carefully".
"""

from __future__ import annotations

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.bootstrap import Container
from insightlm.identity.adapters.models import MembershipRow, TenantRow
from insightlm.identity.application.commands import RegisterOwnerCommand
from insightlm.shared.adapters.db.base import clear_tenant_context, set_tenant_context
from insightlm.shared.domain.ids import TenantId
from tests.conftest import requires_docker

pytestmark = [pytest.mark.integration, requires_docker]


async def register(container: Container, slug: str) -> TenantId:
    result = await container.register_owner.execute(
        RegisterOwnerCommand(
            tenant_name=slug.title(),
            tenant_slug=slug,
            email=f"owner@{slug}.example.com",
            display_name=f"{slug} owner",
            password="correct-horse-battery-staple",
        )
    )
    return result.tenant.id


async def test_a_tenant_sees_only_its_own_rows(
    container: Container, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    acme = await register(container, "acme")
    globex = await register(container, "globex")

    async with session_factory() as session:
        await session.begin()
        await set_tenant_context(session, acme)

        visible = (await session.execute(select(TenantRow.id))).scalars().all()
        assert visible == [acme]
        assert globex not in visible

        memberships = (await session.execute(select(MembershipRow.tenant_id))).scalars().all()
        assert set(memberships) == {acme}


async def test_rls_still_blocks_when_the_application_filter_is_removed(
    container: Container, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Defence in depth, demonstrated.

    This query has no WHERE clause at all -- it is exactly the bug a tired developer
    ships on a Friday. Postgres returns nothing anyway, because the policy is not
    something the application can forget.
    """
    acme = await register(container, "acme")
    await register(container, "globex")

    async with session_factory() as session:
        await session.begin()
        await set_tenant_context(session, acme)

        # Deliberately unfiltered. In an application-filtering design this leaks
        # every tenant's memberships.
        rows = (await session.execute(text("SELECT tenant_id FROM memberships"))).scalars().all()

        assert len(rows) == 1
        assert rows[0] == acme


async def test_without_tenant_context_nothing_is_visible(
    container: Container, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Fails closed, not open.

    An unset context makes every policy comparison NULL, so the query returns nothing.
    The opposite default -- "no context means no restriction" -- is how these systems
    usually leak.
    """
    await register(container, "acme")
    await register(container, "globex")

    async with session_factory() as session:
        await session.begin()
        await clear_tenant_context(session)

        assert (await session.execute(select(TenantRow))).scalars().all() == []
        assert (await session.execute(select(MembershipRow))).scalars().all() == []


async def test_writing_into_another_tenant_is_rejected(
    container: Container, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """The WITH CHECK half of the policy: you cannot plant rows in someone else's tenant."""
    acme = await register(container, "acme")
    globex = await register(container, "globex")

    async with session_factory() as session:
        await session.begin()
        await set_tenant_context(session, acme)

        with pytest.raises(Exception, match="row-level security"):
            await session.execute(
                text(
                    "INSERT INTO invitations "
                    "(id, tenant_id, email, role, token_hash, invited_by, expires_at) "
                    "VALUES (gen_random_uuid(), :tenant, 'x@example.com', 'recruiter', "
                    "'deadbeef', (SELECT id FROM users LIMIT 1), now() + interval '1 day')"
                ),
                {"tenant": globex},
            )
        await session.rollback()
