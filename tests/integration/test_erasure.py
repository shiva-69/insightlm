"""Erasure: every trace gone, proven by searching for it -- and the search itself tested."""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text

from insightlm.bootstrap import Container, build_role_catalogue
from insightlm.retrieval.application.answer import AnswerRequest
from insightlm.shared.domain.policy import Actor
from insightlm.shared.domain.roles import PlatformRole
from tests.conftest import requires_docker
from tests.integration.test_recruiting_flow import (
    STRONG_CV,
    add_candidate,
    auth,
    requisition_with_rubric,
    upload,
    workspace,
)

pytestmark = [pytest.mark.integration, requires_docker]

CV = ["Asha Rao", "asha.rao@example.com", *STRONG_CV]


async def _used_candidate(client: AsyncClient, container: Container) -> dict[str, Any]:
    """A candidate who has been through everything that leaves a trace."""
    tokens = await workspace(client)
    recruiter, owner = tokens["recruiter"], tokens["owner"]
    document_id = await upload(client, container, recruiter, CV, "Asha Rao CV")
    requisition_id, rubric_id = await requisition_with_rubric(client, recruiter)
    await client.post(f"/rubrics/{rubric_id}/approve", headers=auth(tokens["hiring_manager"]))
    added = await add_candidate(client, recruiter, requisition_id, "Asha Rao", document_id)
    application_id = added["application"]["id"]
    assert (
        await client.post(f"/applications/{application_id}/assessments", headers=auth(recruiter))
    ).status_code == 201
    await client.post(
        f"/applications/{application_id}/stage", json={"to": "screened"}, headers=auth(recruiter)
    )

    put = await client.put(
        f"/candidates/{added['candidate']['id']}/self-identification",
        json={"dimension": "gender", "value": "woman"},
        headers=auth(owner),
    )
    assert put.status_code == 204, put.text

    # A saved answer that names her and cites her CV.
    claims = container.token_issuer.verify_access_token(owner)
    actor = Actor.of(
        build_role_catalogue(),
        user_id=claims.user_id,
        tenant_id=claims.tenant_id,
        role=PlatformRole.OWNER,
    )
    await container.retrieval.ask.ask(actor, AnswerRequest(question="Has Asha Rao run Kubernetes?"))
    return {
        "tokens": tokens,
        "actor": actor,
        "candidate_id": added["candidate"]["id"],
        "document_id": document_id,
        "requisition_id": requisition_id,
    }


async def _count(container: Container, actor: Actor, sql: str, **params: Any) -> int:
    from insightlm.shared.adapters.db.base import set_tenant_context

    async with container.session_factory() as session:
        await session.begin()
        await set_tenant_context(session, actor.tenant_id)
        return int((await session.execute(text(sql), params)).scalar_one())


async def test_erasure_removes_every_trace_and_proves_it(
    client: AsyncClient, container: Container
) -> None:
    ctx = await _used_candidate(client, container)
    actor: Actor = ctx["actor"]
    assert await _count(container, actor, "SELECT count(*) FROM answers") == 1

    response = await client.post(
        f"/candidates/{ctx['candidate_id']}/erasure", headers=auth(ctx["tokens"]["owner"])
    )
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["verified"] is True, report["residue"]
    assert report["documents_erased"] == 1
    assert report["files_deleted"] == 1
    assert report["answers_deleted"] == 1

    # Independently of the report: nothing left where it lived.
    doc = UUID(ctx["document_id"])
    for sql in (
        "SELECT count(*) FROM candidates",
        "SELECT count(*) FROM applications",
        "SELECT count(*) FROM assessments",
        "SELECT count(*) FROM stage_transitions",
        "SELECT count(*) FROM answers",
        "SELECT count(*) FROM chunks",
        "SELECT count(*) FROM document_texts",
        "SELECT count(*) FROM self_identifications",
    ):
        assert await _count(container, actor, sql) == 0, sql
    assert (
        await _count(container, actor, "SELECT count(*) FROM documents WHERE id = :d", d=doc) == 0
    )

    # Her CV can no longer be retrieved by anyone.
    evidence = await container.retrieval.ask.retrieve(
        actor, AnswerRequest(question="Kubernetes payments")
    )
    assert evidence.is_empty

    # The erasure itself is on the record, without naming her; the chain holds.
    entries = (
        await client.get("/audit/entries?limit=200", headers=auth(ctx["tokens"]["owner"]))
    ).json()
    assert {"erasure.requested", "erasure.completed"} <= {e["action"] for e in entries}
    assert "Asha" not in str(entries)
    verified = await client.post("/audit/verify", headers=auth(ctx["tokens"]["owner"]))
    assert verified.json()["is_intact"] is True


async def test_verification_catches_what_the_saga_does_not_clean(
    client: AsyncClient, container: Container
) -> None:
    """The negative control. A residue search that never finds anything proves nothing."""
    ctx = await _used_candidate(client, container)
    # A recruiter's free-text note elsewhere that names her. Erasure doesn't know it's there.
    async with container.session_factory() as session:
        from insightlm.shared.adapters.db.base import set_tenant_context

        await session.begin()
        await set_tenant_context(session, ctx["actor"].tenant_id)
        await session.execute(
            text("UPDATE requisitions SET description = 'Referred by Asha Rao' WHERE id = :id"),
            {"id": UUID(ctx["requisition_id"])},
        )
        await session.commit()

    report = (
        await client.post(
            f"/candidates/{ctx['candidate_id']}/erasure", headers=auth(ctx["tokens"]["owner"])
        )
    ).json()
    assert report["verified"] is False
    assert {"table": "requisitions", "column": "description", "fingerprint": "name"} in report[
        "residue"
    ]


async def test_a_document_shared_with_another_candidate_is_kept(
    client: AsyncClient, container: Container
) -> None:
    ctx = await _used_candidate(client, container)
    recruiter = ctx["tokens"]["recruiter"]
    await add_candidate(
        client, recruiter, ctx["requisition_id"], "Asha Duplicate", ctx["document_id"]
    )

    report = (
        await client.post(
            f"/candidates/{ctx['candidate_id']}/erasure", headers=auth(ctx["tokens"]["owner"])
        )
    ).json()
    assert report["documents_kept_shared"] == 1
    assert report["documents_erased"] == 0


async def test_only_the_owner_can_erase(client: AsyncClient, container: Container) -> None:
    ctx = await _used_candidate(client, container)
    response = await client.post(
        f"/candidates/{ctx['candidate_id']}/erasure", headers=auth(ctx["tokens"]["recruiter"])
    )
    assert response.status_code == 403
