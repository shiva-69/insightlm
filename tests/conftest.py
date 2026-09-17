"""Shared fixtures.

Integration tests run against a real Postgres with pgvector via testcontainers -- not
a mock, and not SQLite. Row-level security, advisory locks, triggers and JSONB are the
things under test here; a fake would test nothing.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator, Iterator
from typing import TYPE_CHECKING

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.bootstrap import Container, build_container
from insightlm.settings import Settings

if TYPE_CHECKING:
    from httpx import AsyncClient

POSTGRES_IMAGE = "pgvector/pgvector:pg17"


def _docker_available() -> bool:
    import shutil
    import subprocess

    if shutil.which("docker") is None:
        return False
    return (
        subprocess.run(
            ["docker", "info"],  # noqa: S607
            capture_output=True,
            check=False,
        ).returncode
        == 0
    )


requires_docker = pytest.mark.skipif(
    not _docker_available(), reason="Docker is not available for testcontainers"
)


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    """One container for the whole session; each test cleans up after itself."""
    from testcontainers.community.postgres import PostgresContainer

    with PostgresContainer(
        POSTGRES_IMAGE, username="insightlm", password="insightlm", dbname="insightlm"
    ) as container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(5432)
        yield f"postgresql+asyncpg://insightlm:insightlm@{host}:{port}/insightlm"


@pytest.fixture(scope="session")
def migrated_database(postgres_url: str) -> Iterator[str]:
    """Run the real Alembic migrations.

    Applying the actual migration -- rather than ``metadata.create_all`` -- is what
    makes the RLS policies and the append-only trigger part of what is tested. A
    schema created any other way would silently omit them.
    """
    from alembic import command
    from alembic.config import Config

    previous = os.environ.get("DATABASE_URL")
    os.environ["DATABASE_URL"] = postgres_url
    Settings.model_config["env_file"] = None  # ignore a developer's local .env
    try:
        from insightlm.settings import get_settings

        get_settings.cache_clear()
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", get_settings().sync_database_url)
        command.upgrade(config, "head")
        yield postgres_url
    finally:
        if previous is None:
            os.environ.pop("DATABASE_URL", None)
        else:
            os.environ["DATABASE_URL"] = previous
        from insightlm.settings import get_settings

        get_settings.cache_clear()


@pytest.fixture
def settings(migrated_database: str) -> Settings:
    return Settings(
        environment="ci",
        database_url=migrated_database,  # type: ignore[arg-type]
        jwt_secret="test-secret-not-used-anywhere-real",  # type: ignore[arg-type]
        log_json=False,
        log_level="WARNING",
    )


@pytest.fixture
async def container(settings: Settings) -> AsyncIterator[Container]:
    """A wired container over an empty database.

    Truncation happens here, on the way in, rather than in an autouse teardown. A test
    that fails mid-transaction can skip its teardown, and then every later test
    inherits the mess -- cleaning on setup makes each test's starting state
    unconditional. Doing it inside this fixture also means unit tests, which never ask
    for a container, never pay for a Postgres container.

    TRUNCATE does not fire row-level DELETE triggers, so the append-only guard on
    `audit_entries` does not block it. That is what we want between tests, and
    emphatically not what we want at runtime -- see the trigger tests.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    built = build_container(settings)
    # Truncation runs as the owner: the application role is deliberately denied
    # TRUNCATE on audit_entries, which is the point of the grant.
    admin_engine = create_async_engine(str(settings.database_url))
    admin_sessions = async_sessionmaker(bind=admin_engine, expire_on_commit=False)
    try:
        async with admin_sessions() as session:
            await session.begin()
            await session.execute(
                text(
                    "TRUNCATE outbox_events, audit_entries, invitations, memberships, "
                    "users, tenants RESTART IDENTITY CASCADE"
                )
            )
            await session.commit()
        yield built
    finally:
        await admin_engine.dispose()
        await built.dispose()


@pytest.fixture
def session_factory(container: Container) -> async_sessionmaker[AsyncSession]:
    """Sessions as the *restricted* application role -- subject to RLS."""
    return container.session_factory


@pytest.fixture
async def admin_session_factory(
    settings: Settings,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Sessions as the privileged owner -- bypasses RLS and owns the tables.

    Used for two things only: truncating between tests, and standing in for an
    attacker who has obtained elevated access, so the tamper-evidence tests can prove
    the hash chain still catches them.
    """
    from sqlalchemy.ext.asyncio import create_async_engine

    engine = create_async_engine(str(settings.database_url), poolclass=None)
    try:
        yield async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def client(container: Container) -> AsyncIterator[AsyncClient]:
    """An HTTP client bound to the app, wired to the test container.

    ``create_app`` takes the container so tests never reach into module globals to
    swap dependencies.
    """
    from httpx import ASGITransport, AsyncClient

    from insightlm.api.app import create_app

    app = create_app(container.settings, container=container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http_client:
        # The lifespan never runs under ASGITransport, so set state directly.
        app.state.container = container
        yield http_client
