"""The worker's entry points, driven the way ARQ drives them, with a recording queue."""

from __future__ import annotations

from typing import Any

import pytest

from insightlm.bootstrap import Container
from insightlm.documents.application.upload import UploadDocument
from insightlm.worker import main as worker
from tests.conftest import requires_docker
from tests.fakes import FlakyObjectStore, InMemoryObjectStore
from tests.integration.test_document_engine import build_resume_pdf, owner_of, pipeline_with

pytestmark = [pytest.mark.integration, requires_docker]


class RecordingQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def enqueue_job(self, *args: Any, **kwargs: Any) -> None:
        self.jobs.append((args, kwargs))


async def test_the_relay_hands_each_upload_to_the_worker_with_its_trace(
    container: Container,
) -> None:
    actor = await owner_of(container)
    upload = UploadDocument(container.documents_uow, InMemoryObjectStore(), container.clock)  # type: ignore[arg-type]
    result = await upload.execute(actor, filename="cv.pdf", payload=build_resume_pdf())

    queue = RecordingQueue()
    published = await worker.drain_outbox({"container": container, "redis": queue})

    # Registration writes an event nothing consumes yet: marked done, never enqueued.
    assert published >= 1
    [(args, kwargs)] = queue.jobs
    assert args == (worker.INGEST_TASK, str(actor.tenant_id), str(result.version.id))
    assert "trace" in kwargs, "the event's trace context rides along to the worker"
    assert await worker.drain_outbox({"container": container, "redis": queue}) == 0


async def test_the_ingest_task_completes_or_schedules_its_own_retry(container: Container) -> None:
    actor = await owner_of(container)
    store = FlakyObjectStore(fail_times=0)
    upload = UploadDocument(container.documents_uow, store, container.clock)  # type: ignore[arg-type]
    result = await upload.execute(actor, filename="cv.pdf", payload=build_resume_pdf())

    # First run: storage fails once, so the task must schedule the retry itself.
    store._remaining_failures = 1
    from dataclasses import replace

    flaky = replace(container, ingestion=pipeline_with(container, store))
    queue = RecordingQueue()
    ctx = {"container": flaky, "redis": queue}
    first = await worker.ingest_document_version(ctx, str(actor.tenant_id), str(result.version.id))
    assert first["completed"] is False and first["retry_in"] == 5.0
    [(args, kwargs)] = queue.jobs
    assert args[0] == "ingest_document_version"
    assert kwargs["_defer_by"].total_seconds() == 5.0

    second = await worker.ingest_document_version(ctx, str(actor.tenant_id), str(result.version.id))
    assert second["completed"] is True and second["retry_in"] is None
    assert len(queue.jobs) == 1, "a completed job schedules nothing"
