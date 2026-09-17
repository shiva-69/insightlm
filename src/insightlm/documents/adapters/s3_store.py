"""S3-compatible object storage (MinIO locally).

boto3 is synchronous, so every call is dispatched to a worker thread. The alternative,
aioboto3, pins aiobotocore to exact botocore versions -- a dependency headache out of
proportion to putting a few PDFs in a bucket. Uploads are I/O-bound and infrequent;
the thread hop costs nothing measurable.
"""

from __future__ import annotations

import asyncio
from functools import partial
from typing import Any

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from insightlm.settings import Settings
from insightlm.shared.domain.errors import NotFoundError
from insightlm.shared.observability.logging import get_logger

_logger = get_logger(__name__)


class S3ObjectStore:
    def __init__(self, settings: Settings) -> None:
        self._bucket = settings.s3_bucket
        self._client: Any = boto3.client(
            "s3",
            endpoint_url=settings.s3_endpoint_url,
            aws_access_key_id=settings.s3_access_key.get_secret_value(),
            aws_secret_access_key=settings.s3_secret_key.get_secret_value(),
            region_name="us-east-1",
            config=Config(signature_version="s3v4", retries={"max_attempts": 3}),
        )

    async def ensure_bucket(self) -> None:
        """Create the bucket if it is missing, so a clean `docker compose up` works."""
        await asyncio.to_thread(self._ensure_bucket_sync)

    def _ensure_bucket_sync(self) -> None:
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)
            _logger.info("bucket_created", bucket=self._bucket)

    async def put(self, key: str, payload: bytes, *, content_type: str) -> None:
        await asyncio.to_thread(
            partial(
                self._client.put_object,
                Bucket=self._bucket,
                Key=key,
                Body=payload,
                ContentType=content_type,
            )
        )

    async def get(self, key: str) -> bytes:
        try:
            response = await asyncio.to_thread(
                partial(self._client.get_object, Bucket=self._bucket, Key=key)
            )
        except ClientError as exc:
            raise NotFoundError("stored document not found", detail={"key": key}) from exc
        body: bytes = await asyncio.to_thread(response["Body"].read)
        return body

    async def delete(self, key: str) -> None:
        # Idempotent by contract: erasure re-runs, and S3 treats deleting a missing
        # object as success, which is the behaviour we want.
        await asyncio.to_thread(partial(self._client.delete_object, Bucket=self._bucket, Key=key))

    async def presigned_url(self, key: str, *, expires_in: int = 900) -> str:
        url: str = await asyncio.to_thread(
            partial(
                self._client.generate_presigned_url,
                "get_object",
                Params={"Bucket": self._bucket, "Key": key},
                ExpiresIn=expires_in,
            )
        )
        return url
