"""Uploading documents and reading their processing state."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, File, Form, Response, UploadFile, status

from insightlm.api.deps import ActorDep, ContainerDep
from insightlm.api.schemas import (
    DocumentResponse,
    DocumentVersionResponse,
    UploadResponse,
)
from insightlm.shared.domain.errors import NotFoundError
from insightlm.shared.domain.ids import DocumentId

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post(
    "",
    response_model=UploadResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Upload a document for ingestion",
)
async def upload(
    actor: ActorDep,
    container: ContainerDep,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
) -> UploadResponse:
    payload = await file.read()
    result = await container.upload_document.execute(
        actor,
        filename=file.filename or "upload",
        payload=payload,
        title=title,
    )
    return UploadResponse(
        document=DocumentResponse.model_validate(result.document),
        version=DocumentVersionResponse.model_validate(result.version),
        was_duplicate=result.was_duplicate,
    )


@router.get("", response_model=list[DocumentResponse], summary="List documents")
async def list_documents(
    actor: ActorDep, container: ContainerDep, limit: int = 50, offset: int = 0
) -> list[DocumentResponse]:
    factory = container.documents_uow
    async with factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:  # type: ignore[operator]
        documents = await uow.documents.list_for_tenant(
            actor.tenant_id, limit=min(limit, 200), offset=offset
        )
    return [DocumentResponse.model_validate(document) for document in documents]


@router.get(
    "/{document_id}/status",
    response_model=DocumentVersionResponse,
    summary="Ingestion state of a document's latest version",
)
async def document_status(
    document_id: UUID, actor: ActorDep, container: ContainerDep
) -> DocumentVersionResponse:
    factory = container.documents_uow
    async with factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:  # type: ignore[operator]
        version = await uow.versions.latest_for_document(DocumentId(document_id))
        if version is None:
            # 404 rather than 403: another tenant's document must not be distinguishable
            # from one that does not exist.
            raise NotFoundError("document not found")
    return DocumentVersionResponse.model_validate(version)


@router.get(
    "/{document_id}/file",
    summary="A short-lived URL for the original file",
)
async def document_file(
    document_id: UUID, actor: ActorDep, container: ContainerDep
) -> dict[str, str | int]:
    """Returns a presigned URL rather than proxying the bytes.

    The viewer streams the PDF straight from object storage, so range requests work
    and the API never becomes a file server.
    """
    factory = container.documents_uow
    async with factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:  # type: ignore[operator]
        version = await uow.versions.latest_for_document(DocumentId(document_id))
        if version is None:
            raise NotFoundError("document not found")
        key = version.storage_key

    url = await container.object_store.presigned_url(key, expires_in=900)
    return {"url": url, "expires_in": 900}


@router.get("/{document_id}/raw", summary="Stream the original file")
async def document_raw(document_id: UUID, actor: ActorDep, container: ContainerDep) -> Response:
    """Proxies the bytes instead of redirecting.

    The presigned URL above is the production path -- it keeps the API out of the
    file-serving business and lets range requests hit object storage directly. This
    endpoint exists for the bundled viewer, which would otherwise need CORS configured
    on the bucket to fetch a cross-origin URL. It is the pragmatic choice for a demo,
    and the honest note is that it does not belong on a hot path.
    """
    factory = container.documents_uow
    async with factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:  # type: ignore[operator]
        version = await uow.versions.latest_for_document(DocumentId(document_id))
        if version is None:
            raise NotFoundError("document not found")
        key, media_type = version.storage_key, None
        document = await uow.documents.get(DocumentId(document_id))
        if document is not None:
            media_type = document.media_type.value

    payload = await container.object_store.get(key)
    return Response(content=payload, media_type=media_type or "application/octet-stream")
