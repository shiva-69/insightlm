"""Test doubles that satisfy real ports.

An in-memory object store is not a shortcut around testing storage -- it is the port
doing its job. The pipeline tests are about parsing, chunking, embedding and the
citation contract; putting MinIO in the loop would slow every run without exercising
anything those tests assert. The S3 adapter is exercised by `docker compose`.
"""

from __future__ import annotations

from insightlm.shared.domain.errors import NotFoundError


class InMemoryObjectStore:
    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}
        self.put_calls = 0

    async def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        self._objects[key] = payload
        self.put_calls += 1

    async def get(self, key: str) -> bytes:
        if key not in self._objects:
            raise NotFoundError("stored document not found", detail={"key": key})
        return self._objects[key]

    async def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    async def presigned_url(self, key: str, *, expires_in: int = 900) -> str:
        return f"memory://{key}?expires_in={expires_in}"

    async def ensure_bucket(self) -> None:
        return None


class FlakyObjectStore(InMemoryObjectStore):
    """Fails a configured number of times, then succeeds.

    Used to prove the pipeline resumes from its last completed step rather than
    restarting -- the claim that makes `ingestion_steps` worth having.
    """

    def __init__(self, *, fail_times: int) -> None:
        super().__init__()
        self._remaining_failures = fail_times
        self.get_calls = 0

    async def get(self, key: str) -> bytes:
        self.get_calls += 1
        if self._remaining_failures > 0:
            self._remaining_failures -= 1
            raise ConnectionError("object store unavailable")
        return await super().get(key)
