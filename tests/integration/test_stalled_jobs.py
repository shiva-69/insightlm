"""The reaper picks up exactly the jobs nothing else will ever retry."""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.bootstrap import Container
from insightlm.documents.application.upload import UploadDocument
from insightlm.worker.main import stalled_jobs
from tests.conftest import requires_docker
from tests.fakes import InMemoryObjectStore
from tests.integration.test_document_engine import build_resume_pdf, owner_of

pytestmark = [pytest.mark.integration, requires_docker]


async def test_only_forgotten_jobs_are_requeued(
    container: Container, admin_session_factory: async_sessionmaker[AsyncSession]
) -> None:
    actor = await owner_of(container)
    upload = UploadDocument(container.documents_uow, InMemoryObjectStore(), container.clock)  # type: ignore[arg-type]
    versions = []
    for i in range(5):
        result = await upload.execute(
            actor, filename=f"{i}.pdf", payload=build_resume_pdf() + bytes([i])
        )
        versions.append(result.version.id)

    cases = [
        ("failed", 1, "10 minutes"),  # failed, retry never came: requeue
        ("failed", 1, "5 seconds"),  # failed just now, its retry is still pending: leave
        ("failed", 3, "1 hour"),  # attempts exhausted: dead-lettered, leave
        ("running", 1, "30 minutes"),  # a worker died mid-job: requeue
        ("succeeded", 1, "2 hours"),  # done: leave
    ]
    async with admin_session_factory() as session:
        await session.begin()
        for version, (status, attempts, ago) in zip(versions, cases, strict=True):
            await session.execute(
                text(
                    f"UPDATE ingestion_jobs SET status = :s, attempts = :a, "  # noqa: S608
                    f"updated_at = now() - interval '{ago}' WHERE document_version_id = :v"
                ),
                {"s": status, "a": attempts, "v": version},
            )
        await session.commit()

    async with admin_session_factory() as session:
        found = {UUID(v) for _, v, _ in await stalled_jobs(session)}
    assert found == {versions[0], versions[3]}
