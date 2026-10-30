"""What the document engine lets an actor do.

Declared here rather than in the shared kernel because they belong to the engine, and
any vertical built on it grants them to its own roles.
"""

from __future__ import annotations

from insightlm.shared.domain.roles import Permission


class DocumentPermission:
    UPLOAD = Permission("document.upload")
    READ = Permission("document.read", read_only=True)


DOCUMENT_PERMISSIONS: tuple[Permission, ...] = (DocumentPermission.UPLOAD, DocumentPermission.READ)
