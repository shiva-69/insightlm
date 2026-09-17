"""The unit-of-work rules, asserted.

These are the rules that Spring's ``@Transactional`` gives away free and Python makes
you build. Building them means testing them.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.bootstrap import Container
from insightlm.identity.adapters.models import TenantRow
from insightlm.identity.adapters.uow import SqlAlchemyIdentityUnitOfWork
from insightlm.identity.application.commands import RegisterOwnerCommand
from insightlm.identity.domain.entities import Tenant
from insightlm.shared.adapters.db.base import set_tenant_context
from insightlm.shared.domain.errors import InvariantViolationError
from insightlm.shared.domain.ids import new_tenant_id
from tests.conftest import requires_docker

pytestmark = [pytest.mark.integration, requires_docker]


async def test_exiting_without_commit_rolls_back(container: Container) -> None:
    """There is no implicit commit. Forgetting to commit loses the work, loudly."""
    tenant_id = new_tenant_id()

    async with SqlAlchemyIdentityUnitOfWork(
        container.session_factory, container.clock, tenant_id=tenant_id
    ) as uow:
        await uow.tenants.add(
            Tenant(id=tenant_id, name="Ghost", slug="ghost", created_at=container.clock.now())
        )
        # deliberately no commit

    async with SqlAlchemyIdentityUnitOfWork(
        container.session_factory, container.clock, tenant_id=tenant_id
    ) as check:
        assert await check.tenants.get(tenant_id) is None


async def test_an_exception_rolls_back_every_write_in_the_transaction(
    container: Container, admin_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Atomicity across the domain write, the audit entry and the outbox event."""
    tenant_id = new_tenant_id()

    with pytest.raises(RuntimeError, match="boom"):
        async with SqlAlchemyIdentityUnitOfWork(
            container.session_factory, container.clock, tenant_id=tenant_id
        ) as uow:
            await uow.tenants.add(
                Tenant(id=tenant_id, name="Doomed", slug="doomed", created_at=container.clock.now())
            )
            raise RuntimeError("boom")

    async with admin_session_factory() as session:
        count = await session.scalar(
            select(func.count()).select_from(TenantRow).where(TenantRow.id == tenant_id)
        )
        assert count == 0


async def test_committing_twice_is_refused(container: Container) -> None:
    tenant_id = new_tenant_id()
    async with SqlAlchemyIdentityUnitOfWork(
        container.session_factory, container.clock, tenant_id=tenant_id
    ) as uow:
        await uow.tenants.add(
            Tenant(id=tenant_id, name="Once", slug="once", created_at=container.clock.now())
        )
        await uow.commit()
        with pytest.raises(InvariantViolationError, match="already been committed"):
            await uow.commit()


async def test_a_unit_of_work_is_not_reentrant(container: Container) -> None:
    uow = SqlAlchemyIdentityUnitOfWork(
        container.session_factory, container.clock, tenant_id=new_tenant_id()
    )
    async with uow:
        pass
    with pytest.raises(InvariantViolationError, match="not re-entrant"):
        async with uow:
            pass


async def test_using_it_outside_its_context_manager_is_refused(container: Container) -> None:
    uow = SqlAlchemyIdentityUnitOfWork(container.session_factory, container.clock)
    with pytest.raises(InvariantViolationError, match="outside its context manager"):
        _ = uow.session


async def test_concurrent_units_of_work_do_not_share_a_session(container: Container) -> None:
    """The async footgun this design exists to avoid.

    A session shared across ``asyncio.gather`` corrupts in ways that surface as
    impossible-looking bugs. Phase 4 fans out assessment across criteria, so the
    guarantee is asserted here, before anything depends on it.
    """
    sessions: list[int] = []

    async def unit(index: int) -> None:
        async with SqlAlchemyIdentityUnitOfWork(
            container.session_factory, container.clock, tenant_id=new_tenant_id()
        ) as uow:
            sessions.append(id(uow.session))
            await asyncio.sleep(0)  # force interleaving
            await uow.session.execute(text("SELECT CAST(:i AS INTEGER)"), {"i": index})

    await asyncio.gather(*(unit(i) for i in range(8)))

    assert len(sessions) == 8
    assert len(set(sessions)) == 8, "concurrent units of work shared a session"


async def test_tenant_context_does_not_leak_between_pooled_connections(
    container: Container,
) -> None:
    """``SET LOCAL`` is transaction-scoped.

    If it were session-scoped, a pooled connection would carry one tenant's context
    into the next request that checked it out -- a cross-tenant leak that no
    application-layer review would catch.
    """
    first = new_tenant_id()
    async with SqlAlchemyIdentityUnitOfWork(
        container.session_factory, container.clock, tenant_id=first
    ) as uow:
        await uow.tenants.add(
            Tenant(id=first, name="First", slug="first", created_at=container.clock.now())
        )
        await uow.commit()

    # A later unit of work with no tenant context must see nothing, even though the
    # connection underneath it may well be the same one.
    async with SqlAlchemyIdentityUnitOfWork(container.session_factory, container.clock) as bare:
        assert await bare.tenants.get(first) is None


async def test_audit_and_domain_write_commit_together(container: Container) -> None:
    result = await container.register_owner.execute(
        RegisterOwnerCommand(
            tenant_name="Atomic",
            tenant_slug="atomic",
            email="owner@atomic.example.com",
            display_name="Owner",
            password="correct-horse-battery-staple",
        )
    )
    async with SqlAlchemyIdentityUnitOfWork(
        container.session_factory, container.clock, tenant_id=result.tenant.id
    ) as uow:
        entries = await uow.audit.list_for_tenant(result.tenant.id)
        assert len(entries) == 2
        assert await uow.tenants.get(result.tenant.id) is not None


async def test_outbox_events_are_written_in_the_same_transaction(
    container: Container, admin_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    result = await container.register_owner.execute(
        RegisterOwnerCommand(
            tenant_name="Outboxed",
            tenant_slug="outboxed",
            email="owner@outboxed.example.com",
            display_name="Owner",
            password="correct-horse-battery-staple",
        )
    )

    async with admin_session_factory() as session:
        await session.begin()
        await set_tenant_context(session, result.tenant.id)
        rows = (await session.execute(text("SELECT event_type FROM outbox_events"))).scalars().all()

    assert rows == ["tenant.registered"]


async def test_the_relay_drains_pending_events(container: Container) -> None:
    from insightlm.shared.adapters.db.relay import OutboxRelay

    await container.register_owner.execute(
        RegisterOwnerCommand(
            tenant_name="Relayed",
            tenant_slug="relayed",
            email="owner@relayed.example.com",
            display_name="Owner",
            password="correct-horse-battery-staple",
        )
    )

    relay = OutboxRelay(container.session_factory)
    assert await relay.drain() == 1
    # Idempotent: a second pass finds nothing left to publish.
    assert await relay.drain() == 0
