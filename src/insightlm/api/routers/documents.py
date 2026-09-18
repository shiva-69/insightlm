"""Uploading documents and reading their processing state."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, File, Form, Query, Response, UploadFile, status
from pydantic import BaseModel, ConfigDict, Field

from insightlm.api.deps import ActorDep, ContainerDep
from insightlm.api.schemas import (
    MAX_OFFSET,
    DocumentResponse,
    DocumentVersionResponse,
    UploadResponse,
    reject_unstorable,
)
from insightlm.documents.domain.findings import FindingKind
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
    reject_unstorable(file.filename or "")
    reject_unstorable(title or "")
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
    actor: ActorDep,
    container: ContainerDep,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
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


class FindingResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    kind: FindingKind
    text: str
    page: int | None
    excluded: bool = Field(description="Kept out of the index because a reader can't see it.")
    instruction_like: bool
    rect: dict[str, float] | None


@router.get(
    "/{document_id}/findings",
    response_model=list[FindingResponse],
    summary="Hidden or instruction-like content found in the latest version",
)
async def document_findings(
    document_id: UUID, actor: ActorDep, container: ContainerDep
) -> list[FindingResponse]:
    """What a reviewer should know before trusting anything a model says about this file.

    Concealed text is listed with its location so it can be drawn on the page: the point
    is to show a person what was hidden from them, not merely that something was.
    """
    factory = container.documents_uow
    async with factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:  # type: ignore[operator]
        version = await uow.versions.latest_for_document(DocumentId(document_id))
        if version is None:
            raise NotFoundError("document not found")
        extracted = await uow.versions.load_text(version.id)

    return [
        FindingResponse(
            kind=f.kind,
            text=f.text,
            page=f.page,
            excluded=f.excluded,
            instruction_like=f.instruction_like,
            rect=(
                {
                    "page": f.rect.page,
                    "x0": f.rect.x0,
                    "y0": f.rect.y0,
                    "x1": f.rect.x1,
                    "y1": f.rect.y1,
                }
                if f.rect
                else None
            ),
        )
        for f in (extracted.findings if extracted else ())
    ]
