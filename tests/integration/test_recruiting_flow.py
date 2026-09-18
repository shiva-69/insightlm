"""The Phase 4 exit criterion, end to end, over HTTP.

A recruiter opens a requisition and proposes criteria; nobody can be assessed until a
*different* person approves them; then every verdict in an assessment cites a passage
from that candidate's own documents, and the score is arithmetic over those verdicts.

Runs with the offline judge, so it costs nothing and gives the same answer every time.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pymupdf
import pytest
from httpx import AsyncClient

from insightlm.bootstrap import Container
from insightlm.shared.domain.ids import DocumentVersionId
from tests.conftest import requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

PASSWORD = "correct-horse-battery-staple"

STRONG_CV = [
    "EXPERIENCE",
    "Senior engineer on a payments platform, 2019 to 2025.",
    "Built settlement and reconciliation services in Python and PostgreSQL.",
    "Operated Kubernetes clusters in production across three regions.",
    "Designed idempotent retries for card payment processing.",
]
WEAK_CV = [
    "EXPERIENCE",
    "Graphic designer at a small studio, 2021 to 2025.",
    "Produced brand identities, posters and illustration for clients.",
    "Comfortable with Figma and Adobe Illustrator.",
]

CRITERIA = [
    {
        "label": "Python backend services",
        "kind": "must_have",
        "weight": 1.0,
        "evidence_hint": "built services in Python",
        "disqualifying": True,
    },
    {
        "label": "Payment processing",
        "kind": "strong_preference",
        "weight": 0.7,
        "evidence_hint": "settlement, reconciliation, card payments",
    },
    {
        "label": "Kubernetes in production",
        "kind": "nice_to_have",
        "weight": 0.3,
    },
]


def pdf(lines: list[str]) -> bytes:
    doc = pymupdf.open()
    page = doc.new_page()
    for index, line in enumerate(lines):
        page.insert_text((72, 80 + index * 18), line, fontsize=14 if index == 0 else 10)
    payload: bytes = doc.tobytes()
    doc.close()
    return payload


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def login(client: AsyncClient, email: str) -> str:
    response = await client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200, response.text
    token: str = response.json()["access_token"]
    return token


async def workspace(client: AsyncClient, slug: str = "acme") -> dict[str, str]:
    """An owner, a recruiter and a hiring manager, each with a token."""
    registered = await client.post(
        "/auth/register",
        json={
            "tenant_name": slug.title(),
            "tenant_slug": slug,
            "email": f"owner@{slug}.example.com",
            "display_name": "Owner",
            "password": PASSWORD,
        },
    )
    assert registered.status_code == 201, registered.text
    tokens = {"owner": await login(client, f"owner@{slug}.example.com")}

    for role in ("recruiter", "hiring_manager"):
        email = f"{role}@{slug}.example.com"
        invite = await client.post(
            "/members/invitations",
            json={"email": email, "role": role},
            headers=auth(tokens["owner"]),
        )
        assert invite.status_code == 201, invite.text
        accepted = await client.post(
            "/auth/invitations/accept",
            json={"token": invite.json()["token"], "display_name": role, "password": PASSWORD},
        )
        assert accepted.status_code == 201, accepted.text
        tokens[role] = await login(client, email)
    return tokens


async def upload(
    client: AsyncClient, container: Container, token: str, lines: list[str], title: str
) -> str:
    response = await client.post(
        "/documents",
        files={"file": (f"{title}.pdf", pdf(lines), "application/pdf")},
        data={"title": title},
        headers=auth(token),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    # The worker would pick this up from the outbox; here it runs inline.
    claims = container.token_issuer.verify_access_token(token)
    outcome = await container.ingestion.run(
        tenant_id=claims.tenant_id, version_id=DocumentVersionId(UUID(body["version"]["id"]))
    )
    assert outcome.completed, outcome.error
    document_id: str = body["document"]["id"]
    return document_id


async def requisition_with_rubric(client: AsyncClient, token: str) -> tuple[str, str]:
    created = await client.post(
        "/requisitions",
        json={"title": "Senior Payments Engineer", "description": "Owns settlement."},
        headers=auth(token),
    )
    assert created.status_code == 201, created.text
    requisition_id: str = created.json()["id"]

    proposed = await client.post(
        f"/requisitions/{requisition_id}/rubrics",
        json={"criteria": CRITERIA},
        headers=auth(token),
    )
    assert proposed.status_code == 201, proposed.text
    assert proposed.json()["status"] == "draft"
    rubric_id: str = proposed.json()["id"]
    return requisition_id, rubric_id


async def add_candidate(
    client: AsyncClient, token: str, requisition_id: str, name: str, document_id: str
) -> dict[str, Any]:
    response = await client.post(
        f"/requisitions/{requisition_id}/candidates",
        json={
            "name": name,
            "email": f"{name.lower().replace(' ', '.')}@mail.example.com",
            "document_ids": [document_id],
        },
        headers=auth(token),
    )
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_assessment_is_refused_until_someone_else_approves_the_rubric(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    recruiter, manager = tokens["recruiter"], tokens["hiring_manager"]

    document_id = await upload(client, container, recruiter, STRONG_CV, "Asha CV")
    requisition_id, rubric_id = await requisition_with_rubric(client, recruiter)
    added = await add_candidate(client, recruiter, requisition_id, "Asha", document_id)
    application_id = added["application"]["id"]

    # No approved rubric: refused, and nothing is written.
    refused = await client.post(
        f"/applications/{application_id}/assessments", headers=auth(recruiter)
    )
    assert refused.status_code == 409, refused.text
    assert "approved rubric" in refused.json()["message"]

    # The proposer cannot approve their own bar.
    self_approval = await client.post(f"/rubrics/{rubric_id}/approve", headers=auth(recruiter))
    assert self_approval.status_code == 403, self_approval.text

    approved = await client.post(f"/rubrics/{rubric_id}/approve", headers=auth(manager))
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    assessed = await client.post(
        f"/applications/{application_id}/assessments", headers=auth(recruiter)
    )
    assert assessed.status_code == 201, assessed.text


async def test_every_verdict_cites_the_candidates_own_documents(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    recruiter, manager = tokens["recruiter"], tokens["hiring_manager"]

    strong_doc = await upload(client, container, recruiter, STRONG_CV, "Asha CV")
    weak_doc = await upload(client, container, recruiter, WEAK_CV, "Ben CV")
    requisition_id, rubric_id = await requisition_with_rubric(client, recruiter)
    await client.post(f"/rubrics/{rubric_id}/approve", headers=auth(manager))

    asha = await add_candidate(client, recruiter, requisition_id, "Asha", strong_doc)
    ben = await add_candidate(client, recruiter, requisition_id, "Ben", weak_doc)

    results = {}
    for name, added in (("asha", asha), ("ben", ben)):
        response = await client.post(
            f"/applications/{added['application']['id']}/assessments", headers=auth(recruiter)
        )
        assert response.status_code == 201, response.text
        results[name] = response.json()

    # Scoping: the evidence for a candidate comes from that candidate's documents and
    # nobody else's -- Ben must not be credited with Asha's Kubernetes.
    for name, own_doc in (("asha", strong_doc), ("ben", weak_doc)):
        for judgment in results[name]["judgments"]:
            if judgment["verdict"] == "absent":
                assert judgment["evidence"] == []
                continue
            assert judgment["evidence"], "a positive verdict with no citation"
            assert {e["document_id"] for e in judgment["evidence"]} == {own_doc}
            # Enough to highlight the passage in the viewer, and it survives a reload.
            assert all(e["rects"] and e["document_title"] for e in judgment["evidence"])

    assert results["asha"]["score"] > results["ben"]["score"]

    # Ben has no Python: flagged for a human, not rejected.
    assert "missing_must_have" in results["ben"]["flags"]
    assert results["ben"]["needs_human_review"] is True

    # Provenance on every assessment.
    for assessment in results.values():
        assert assessment["rubric_version"] == 1
        assert assessment["model_version"] == "overlap-judge-v1"
        assert len(assessment["retrieval_config_hash"]) == 64

    shortlist = await client.get(f"/requisitions/{requisition_id}/shortlist", headers=auth(manager))
    assert shortlist.status_code == 200, shortlist.text
    assert [e["candidate"]["name"] for e in shortlist.json()] == ["Asha", "Ben"]
    # Read back from the database, the evidence still carries its geometry.
    reloaded = shortlist.json()[0]["assessment"]
    assert reloaded["judgments"] == results["asha"]["judgments"]

    # And every step of it is on the tamper-evident record.
    verified = await client.post("/audit/verify", headers=auth(tokens["owner"]))
    assert verified.json()["is_intact"] is True
    actions = {
        e["action"]
        for e in (
            await client.get("/audit/entries?limit=200", headers=auth(tokens["owner"]))
        ).json()
    }
    assert {
        "requisition.created",
        "rubric.proposed",
        "rubric.approved",
        "candidate.added",
        "assessment.created",
    } <= actions


async def test_a_new_rubric_version_supersedes_the_old_one(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    recruiter, manager = tokens["recruiter"], tokens["hiring_manager"]
    requisition_id, first = await requisition_with_rubric(client, recruiter)
    await client.post(f"/rubrics/{first}/approve", headers=auth(manager))

    second = await client.post(
        f"/requisitions/{requisition_id}/rubrics",
        json={"criteria": CRITERIA[:2]},
        headers=auth(recruiter),
    )
    assert second.json()["version"] == 2
    await client.post(f"/rubrics/{second.json()['id']}/approve", headers=auth(manager))

    detail = (await client.get(f"/requisitions/{requisition_id}", headers=auth(manager))).json()
    statuses = {r["version"]: r["status"] for r in detail["rubrics"]}
    assert statuses == {1: "superseded", 2: "approved"}


async def test_a_candidate_cannot_point_at_documents_that_do_not_exist(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    requisition_id, _ = await requisition_with_rubric(client, tokens["recruiter"])
    response = await client.post(
        f"/requisitions/{requisition_id}/candidates",
        json={"name": "Ghost", "document_ids": [str(uuid4())]},
        headers=auth(tokens["recruiter"]),
    )
    assert response.status_code == 422, response.text


async def test_another_tenants_documents_are_indistinguishable_from_missing(
    client: AsyncClient, container: Container
) -> None:
    acme = await workspace(client, "acme")
    globex = await workspace(client, "globex")
    globex_doc = await upload(client, container, globex["recruiter"], STRONG_CV, "Their CV")

    requisition_id, _ = await requisition_with_rubric(client, acme["recruiter"])
    response = await client.post(
        f"/requisitions/{requisition_id}/candidates",
        json={"name": "Poached", "document_ids": [globex_doc]},
        headers=auth(acme["recruiter"]),
    )
    assert response.status_code == 422, response.text


async def test_requisitions_are_invisible_across_tenants(
    client: AsyncClient, container: Container
) -> None:
    acme = await workspace(client, "acme")
    globex = await workspace(client, "globex")
    requisition_id, _ = await requisition_with_rubric(client, acme["recruiter"])

    assert (await client.get("/requisitions", headers=auth(globex["owner"]))).json() == []
    peek = await client.get(f"/requisitions/{requisition_id}", headers=auth(globex["owner"]))
    assert peek.status_code == 404


async def test_an_interviewer_cannot_run_an_assessment(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    invite = await client.post(
        "/members/invitations",
        json={"email": "interviewer@acme.example.com", "role": "interviewer"},
        headers=auth(tokens["owner"]),
    )
    await client.post(
        "/auth/invitations/accept",
        json={"token": invite.json()["token"], "display_name": "I", "password": PASSWORD},
    )
    interviewer = await login(client, "interviewer@acme.example.com")

    document_id = await upload(client, container, tokens["recruiter"], STRONG_CV, "CV")
    requisition_id, rubric_id = await requisition_with_rubric(client, tokens["recruiter"])
    await client.post(f"/rubrics/{rubric_id}/approve", headers=auth(tokens["hiring_manager"]))
    added = await add_candidate(client, tokens["recruiter"], requisition_id, "Asha", document_id)

    response = await client.post(
        f"/applications/{added['application']['id']}/assessments", headers=auth(interviewer)
    )
    assert response.status_code == 403
