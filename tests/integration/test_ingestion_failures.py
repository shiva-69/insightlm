"""A document either finishes, is retried on a schedule, or fails with a reason.

Before this, a failed step recorded "attempt 1" and nothing ever ran attempt 2: the
document sat at "processing" indefinitely and the bulk upload screen waited on it.
"""

from __future__ import annotations

import pytest

from insightlm.bootstrap import Container
from insightlm.documents.application.upload import UploadDocument
from insightlm.documents.domain.entities import DocumentStatus
from tests.conftest import requires_docker
from tests.fakes import FlakyObjectStore, InMemoryObjectStore
from tests.integration.test_document_engine import build_resume_pdf, owner_of, pipeline_with

pytestmark = [pytest.mark.integration, requires_docker]


async def test_an_unreadable_file_fails_at_once_with_its_reason(container: Container) -> None:
    actor = await owner_of(container)
    store = InMemoryObjectStore()
    upload = UploadDocument(container.documents_uow, store, container.clock)  # type: ignore[arg-type]
    result = await upload.execute(
        actor, filename="broken.pdf", payload=b"%PDF-1.4 not really a pdf", title="broken"
    )

    outcome = await pipeline_with(container, store).run(
        tenant_id=actor.tenant_id, version_id=result.version.id
    )

    assert outcome.dead_lettered, "retrying a corrupt file only delays telling the user"
    assert outcome.retry_in is None
    async with container.documents_uow(tenant_id=actor.tenant_id) as uow:  # type: ignore[operator]
        version = await uow.versions.get(result.version.id)
    assert version.status is DocumentStatus.FAILED
    assert "could not be opened" in (version.failure_reason or "")


async def test_a_transient_failure_is_scheduled_for_retry(container: Container) -> None:
    actor = await owner_of(container)
    store = FlakyObjectStore(fail_times=1)
    upload = UploadDocument(container.documents_uow, store, container.clock)  # type: ignore[arg-type]
    result = await upload.execute(actor, filename="cv.pdf", payload=build_resume_pdf(), title="CV")

    first = await pipeline_with(container, store).run(
        tenant_id=actor.tenant_id, version_id=result.version.id
    )
    assert not first.completed and not first.dead_lettered
    assert first.retry_in == 5.0

    second = await pipeline_with(container, store).run(
        tenant_id=actor.tenant_id, version_id=result.version.id
    )
    assert second.completed and second.retry_in is None
