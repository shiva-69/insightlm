"""The audit log, read-only by construction."""

from __future__ import annotations

from fastapi import APIRouter, Query

from insightlm.api.deps import ActorDep, ContainerDep
from insightlm.api.schemas import MAX_OFFSET, AuditEntryResponse, ChainVerificationResponse

router = APIRouter(prefix="/audit", tags=["audit"])


@router.get("/entries", response_model=list[AuditEntryResponse], summary="Read the audit log")
async def list_entries(
    actor: ActorDep,
    container: ContainerDep,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0, le=MAX_OFFSET),
) -> list[AuditEntryResponse]:
    entries = await container.governance.list_audit_entries.execute(
        actor, limit=limit, offset=offset
    )
    return [AuditEntryResponse.model_validate(entry) for entry in entries]


@router.post(
    "/verify",
    response_model=ChainVerificationResponse,
    summary="Verify the hash chain is unbroken",
)
async def verify_chain(actor: ActorDep, container: ContainerDep) -> ChainVerificationResponse:
    result = await container.governance.verify_audit_chain.execute(actor)
    return ChainVerificationResponse.model_validate(result)
