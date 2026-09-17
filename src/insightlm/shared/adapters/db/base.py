"""SQLAlchemy foundations: metadata, engine, session factory, tenant context.

The ORM classes in this package are *persistence* models. Domain entities are plain
frozen dataclasses that know nothing about SQLAlchemy -- repositories translate
between the two. That separation is what lets the ``domain-purity`` contract hold.
"""

from __future__ import annotations

from sqlalchemy import MetaData, text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from insightlm.settings import Settings
from insightlm.shared.domain.ids import TenantId, UserId

# Explicit naming so Alembic autogenerate produces stable, reviewable diffs.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

TENANT_SETTING = "app.tenant_id"
"""Postgres GUC read by every tenant-isolation policy."""

ACTOR_SETTING = "app.user_id"
"""Postgres GUC identifying the acting user.

Needed because two legitimate flows have a user but no tenant yet: logging in, and
listing the workspaces you belong to. Rather than granting those flows an exemption,
the policies express the actual rule -- *a user may always see their own memberships,
and the tenants those memberships are in.*
"""


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


def create_engine(settings: Settings) -> AsyncEngine:
    # Note `app_database_url`, not `database_url`: runtime traffic must never use the
    # privileged role, or row-level security stops applying. See Settings.
    return create_async_engine(
        settings.app_database_url,
        echo=settings.database_echo,
        pool_size=settings.database_pool_size,
        max_overflow=settings.database_max_overflow,
        pool_pre_ping=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
        autoflush=False,
    )


async def set_tenant_context(session: AsyncSession, tenant_id: TenantId) -> None:
    """Scope the current transaction to one tenant.

    ``SET LOCAL`` is transaction-scoped, so the value is discarded on commit or
    rollback and can never leak to the next checkout of a pooled connection.

    Every RLS policy compares against this setting. When it is absent the comparison
    yields NULL, the policy denies, and the query returns nothing -- the system fails
    closed rather than open. That is the entire point of doing tenancy this way.
    """
    await session.execute(
        text(f"SELECT set_config('{TENANT_SETTING}', :tenant_id, true)"),
        {"tenant_id": str(tenant_id)},
    )


async def set_actor_context(session: AsyncSession, user_id: UserId) -> None:
    """Scope the current transaction to one acting user."""
    await session.execute(
        text(f"SELECT set_config('{ACTOR_SETTING}', :user_id, true)"),
        {"user_id": str(user_id)},
    )


async def clear_tenant_context(session: AsyncSession) -> None:
    """Drop tenant scope. Used by tests that prove the policies fail closed."""
    await session.execute(text(f"SELECT set_config('{TENANT_SETTING}', '', true)"))
    await session.execute(text(f"SELECT set_config('{ACTOR_SETTING}', '', true)"))
