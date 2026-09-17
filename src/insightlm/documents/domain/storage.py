"""Where a document's bytes live.

The key *scheme* is a domain decision, not a storage-adapter detail: it determines
content-addressing (identical bytes occupy one object) and tenant partitioning (a
bucket policy or lifecycle rule can operate per tenant). Swapping S3 for anything else
must not change it, which is why it lives here rather than beside the boto3 client.
"""

from __future__ import annotations

from insightlm.shared.domain.ids import TenantId


def storage_key(tenant_id: TenantId, checksum: str) -> str:
    """Content-addressed and tenant-partitioned.

    Keyed by checksum rather than document id, so the same bytes uploaded twice
    occupy one object. The two-character fan-out keeps any single prefix from
    accumulating every object in the tenant.
    """
    return f"tenants/{tenant_id}/documents/{checksum[:2]}/{checksum}"
