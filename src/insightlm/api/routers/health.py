"""Liveness and readiness."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from insightlm.api.deps import ContainerDep

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["up", "down"]
    version: str


@router.get("/health", response_model=HealthResponse, summary="Liveness and dependency check")
async def health(container: ContainerDep) -> HealthResponse:
    from insightlm import __version__

    database: Literal["up", "down"] = "up"
    try:
        async with container.session_factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        database = "down"

    return HealthResponse(
        status="ok" if database == "up" else "degraded",
        database=database,
        version=__version__,
    )
