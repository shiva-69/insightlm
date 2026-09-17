"""The real S3 adapter, against a real MinIO.

The rest of the document tests substitute an in-memory store, because object storage
is not what they assert and a container per run would cost seconds for nothing. That
substitution is only honest if the real adapter is exercised *somewhere* -- otherwise
presigned URLs, content types and the delete-is-idempotent contract are asserted by
nobody.

This is that somewhere.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from insightlm.documents.adapters.s3_store import S3ObjectStore
from insightlm.documents.domain.storage import storage_key
from insightlm.settings import Settings
from insightlm.shared.domain.errors import NotFoundError
from insightlm.shared.domain.ids import new_tenant_id
from tests.conftest import requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

# quay.io, not Docker Hub: MinIO no longer publishes `minio/minio` there, and the
# failure reads like an auth error rather than a missing repository. See compose.yaml.
MINIO_IMAGE = "quay.io/minio/minio:latest"


@pytest.fixture(scope="module")
def minio_endpoint() -> Iterator[str]:
    from testcontainers.core.container import DockerContainer
    from testcontainers.core.wait_strategies import LogMessageWaitStrategy

    container = (
        DockerContainer(MINIO_IMAGE)
        .with_command('server /data --console-address ":9001"')
        .with_env("MINIO_ROOT_USER", "minioadmin")
        .with_env("MINIO_ROOT_PASSWORD", "minioadmin")
        .with_exposed_ports(9000)
        .waiting_for(LogMessageWaitStrategy("MinIO Object Storage Server"))
    )
    with container:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(9000)
        yield f"http://{host}:{port}"


@pytest.fixture
async def store(minio_endpoint: str) -> S3ObjectStore:
    settings = Settings(
        s3_endpoint_url=minio_endpoint,
        s3_bucket="insightlm-test",
        jwt_secret="test-secret-not-used-anywhere-real",  # type: ignore[arg-type]
    )
    adapter = S3ObjectStore(settings)
    await adapter.ensure_bucket()
    return adapter


async def test_a_document_round_trips(store: S3ObjectStore) -> None:
    key = storage_key(new_tenant_id(), "a" * 64)
    payload = b"%PDF-1.7\nnot really a pdf but the bytes must survive\n"

    await store.put(key, payload, content_type="application/pdf")
    assert await store.get(key) == payload


async def test_a_missing_object_raises_a_domain_error(store: S3ObjectStore) -> None:
    """Not a botocore ClientError. The port's callers handle domain errors."""
    with pytest.raises(NotFoundError):
        await store.get("tenants/nobody/documents/ff/does-not-exist")


async def test_delete_is_idempotent(store: S3ObjectStore) -> None:
    """Erasure re-runs, so deleting a missing object has to be success, not an error.

    Phase 6's erasure saga depends on this: a partially-completed erasure must be
    safely repeatable.
    """
    key = storage_key(new_tenant_id(), "b" * 64)
    await store.put(key, b"transient", content_type="text/plain")

    await store.delete(key)
    await store.delete(key)  # second time must not raise

    with pytest.raises(NotFoundError):
        await store.get(key)


async def test_ensure_bucket_is_idempotent(store: S3ObjectStore) -> None:
    """`docker compose up` runs it on every worker start."""
    await store.ensure_bucket()
    await store.ensure_bucket()


async def test_a_presigned_url_serves_the_object_without_credentials(
    store: S3ObjectStore,
) -> None:
    """The production read path: the viewer streams from storage, not through the API."""
    import httpx

    key = storage_key(new_tenant_id(), "c" * 64)
    payload = b"%PDF-1.7\npresigned content\n"
    await store.put(key, payload, content_type="application/pdf")

    url = await store.presigned_url(key, expires_in=300)

    async with httpx.AsyncClient() as client:
        response = await client.get(url)

    assert response.status_code == 200
    assert response.content == payload
    assert response.headers["content-type"] == "application/pdf"


async def test_identical_content_occupies_one_object(store: S3ObjectStore) -> None:
    """Content-addressed keys are what make upload dedup free at the storage layer."""
    tenant = new_tenant_id()
    checksum = "d" * 64
    first = storage_key(tenant, checksum)
    second = storage_key(tenant, checksum)

    assert first == second
    await store.put(first, b"same bytes", content_type="text/plain")
    assert await store.get(second) == b"same bytes"


async def test_keys_are_partitioned_by_tenant(store: S3ObjectStore) -> None:
    """So a bucket policy or lifecycle rule can operate per tenant."""
    a, b = new_tenant_id(), new_tenant_id()
    assert storage_key(a, "e" * 64) != storage_key(b, "e" * 64)
    assert storage_key(a, "e" * 64).startswith(f"tenants/{a}/")
