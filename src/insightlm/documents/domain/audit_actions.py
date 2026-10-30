"""The audit actions the document engine records."""

from __future__ import annotations

from insightlm.governance.domain.audit import AuditAction


class DocumentAudit:
    DOCUMENT_UPLOADED = AuditAction("document.uploaded")
