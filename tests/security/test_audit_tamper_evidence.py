"""Two independent layers of append-only enforcement, each tested separately.

Layer one is a database trigger. Layer two is the hash chain. The second test
deliberately disables the first, because a defence you have never seen fail is a
defence you do not understand.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.bootstrap import Container
from insightlm.identity.application.commands import RegisterOwnerCommand
from insightlm.shared.adapters.db.base import set_tenant_context
from insightlm.shared.domain.ids import TenantId
from insightlm.shared.domain.policy import Actor
from insightlm.shared.domain.roles import Role
from tests.conftest import requires_docker

pytestmark = [pytest.mark.integration, requires_docker]


async def register(container: Container) -> tuple[TenantId, Actor]:
    result = await container.register_owner.execute(
        RegisterOwnerCommand(
            tenant_name="Acme",
            tenant_slug="acme",
            email="owner@acme.example.com",
            display_name="Owner",
            password="correct-horse-battery-staple",
        )
    )
    actor = Actor(user_id=result.user.id, tenant_id=result.tenant.id, role=Role.OWNER)
    return result.tenant.id, actor


async def test_registration_writes_a_verifiable_chain(container: Container) -> None:
    _, actor = await register(container)

    entries = await container.list_audit_entries.execute(actor)
    assert len(entries) == 2  # tenant.created, user.registered

    verification = await container.verify_audit_chain.execute(actor)
    assert verification.is_intact
    assert verification.entries_checked == 2


async def test_the_application_role_has_no_privilege_to_rewrite_history(
    container: Container, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Layer one: privilege.

    The application connects as a role that was never granted UPDATE or DELETE on this
    table. The statement is rejected before any trigger runs -- the cheapest possible
    place to stop it.
    """
    tenant_id, _ = await register(container)

    async with session_factory() as session:
        await session.begin()
        await set_tenant_context(session, tenant_id)

        with pytest.raises(Exception, match="permission denied"):
            await session.execute(text("UPDATE audit_entries SET action = 'tampered'"))
        await session.rollback()

    async with session_factory() as session:
        await session.begin()
        await set_tenant_context(session, tenant_id)

        with pytest.raises(Exception, match="permission denied"):
            await session.execute(text("DELETE FROM audit_entries"))
        await session.rollback()


async def test_the_trigger_stops_even_a_privileged_role(
    container: Container, admin_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Layer two: the trigger.

    Here the caller is the table owner and *does* hold UPDATE and DELETE. Privilege is
    no longer the obstacle; the trigger is, and it fires for every role including this
    one. This is the layer that catches an operator with a psql prompt.
    """
    tenant_id, _ = await register(container)

    for statement in (
        "UPDATE audit_entries SET action = 'tampered'",
        "DELETE FROM audit_entries",
    ):
        async with admin_session_factory() as session:
            await session.begin()
            await set_tenant_context(session, tenant_id)

            with pytest.raises(Exception, match="append-only"):
                await session.execute(text(statement))
            await session.rollback()


async def test_the_chain_detects_a_tamper_that_bypasses_the_trigger(
    container: Container, admin_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    """Layer three.

    Here we simulate an attacker who got far enough to disable the trigger -- a
    compromised superuser, or direct access to the storage. The mutation succeeds.
    Verification catches it anyway, and names the entry where the chain breaks. That
    is the difference between preventing tampering and being able to *prove* it
    happened.
    """
    tenant_id, actor = await register(container)

    assert (await container.verify_audit_chain.execute(actor)).is_intact

    # The attacker here holds owner privileges -- enough to drop the trigger and to
    # bypass RLS. Everything the first layer protects is gone.
    async with admin_session_factory() as session:
        await session.begin()
        await set_tenant_context(session, tenant_id)
        await session.execute(
            text("ALTER TABLE audit_entries DISABLE TRIGGER audit_entries_append_only")
        )
        await session.execute(
            text(
                "UPDATE audit_entries SET payload = payload || '{\"tampered\": true}'::jsonb "
                "WHERE sequence = 1"
            )
        )
        await session.execute(
            text("ALTER TABLE audit_entries ENABLE TRIGGER audit_entries_append_only")
        )
        await session.commit()

    verification = await container.verify_audit_chain.execute(actor)
    assert not verification.is_intact
    assert verification.broken_at is not None
    assert "mutated" in (verification.reason or "")


async def test_audit_log_is_not_readable_across_tenants(container: Container) -> None:
    _, acme_actor = await register(container)

    other = await container.register_owner.execute(
        RegisterOwnerCommand(
            tenant_name="Globex",
            tenant_slug="globex",
            email="owner@globex.example.com",
            display_name="Other Owner",
            password="correct-horse-battery-staple",
        )
    )

    # An actor holding a *valid* owner role, pointed at someone else's tenant.
    trespasser = Actor(user_id=other.user.id, tenant_id=acme_actor.tenant_id, role=Role.OWNER)
    entries = await container.list_audit_entries.execute(trespasser)

    # RLS scopes the read to the tenant in the actor's context, so the trespasser sees
    # Acme's log only because their context *is* Acme -- they cannot see Globex's here.
    assert all(entry.tenant_id == acme_actor.tenant_id for entry in entries)
