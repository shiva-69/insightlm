"""Object storage for the raw bytes."""

from __future__ import annotations

from typing import Protocol


class ObjectStore(Protocol):
    async def ensure_bucket(self) -> None:
        """Make the destination exist. Idempotent, and called on every process start.

        On the port rather than only on the S3 adapter because *every* process that
        writes needs it, not just the worker. Leaving it off meant the API could
        accept an upload and fail at the last step against a bucket nobody had
        created -- which is exactly what happened the first time the API was run
        without the worker.
        """
        ...

    async def put(self, key: str, payload: bytes, *, content_type: str) -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def delete(self, key: str) -> None:
        """Must be idempotent -- erasure re-runs, and a missing object is success."""
        ...

    async def presigned_url(self, key: str, *, expires_in: int = 900) -> str:
        """A short-lived read URL, so the viewer streams the PDF without proxying it
        through the API."""
        ...
