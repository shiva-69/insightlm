"""The second vertical, end to end through the API against real Postgres.

Everything recruiting relies on -- an approved standard before judging, cited findings,
the audit log, workspace isolation, role checks -- holds here too, provided by the
engine rather than rebuilt.
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient

from insightlm.bootstrap import Container
from tests.conftest import requires_docker
from tests.integration.test_recruiting_flow import PASSWORD, auth, login, upload, workspace

pytestmark = [pytest.mark.integration, requires_docker]

CONTRACT = [
    "MASTER SERVICES AGREEMENT",
    "LIMITATION OF LIABILITY",
    "Each party's aggregate liability is capped at 24 months' fees.",
    "TERMINATION",
    "Either party may terminate for convenience on 30 days' written notice.",
    "PAYMENT",
    "Invoices are payable within 45 days of receipt.",
]

POSITIONS = [
    {
        "clause": "Limitation of liability",
        "standard": "Aggregate liability capped at 12 months' fees.",
        "severity": "critical",
        "evidence_hint": "liability, capped, aggregate",
    },
    {
        "clause": "Termination for convenience",
        "standard": "Either party may terminate on 30 days' written notice.",
        "severity": "major",
        "evidence_hint": "terminate, convenience, notice",
    },
    {
        "clause": "Data protection",
        "standard": "Processor terms compliant with GDPR Article 28.",
        "severity": "critical",
        "evidence_hint": "personal data, processor, GDPR",
    },
]


async def invite(client: AsyncClient, owner: str, email: str, role: str) -> str:
    created = await client.post(
        "/members/invitations", json={"email": email, "role": role}, headers=auth(owner)
    )
    assert created.status_code == 201, created.text
    accepted = await client.post(
        "/auth/invitations/accept",
        json={"token": created.json()["token"], "display_name": role, "password": PASSWORD},
    )
    assert accepted.status_code == 201, accepted.text
    return await login(client, email)


async def legal_team(client: AsyncClient, slug: str = "acme") -> dict[str, str]:
    tokens = await workspace(client, slug)
    tokens["manager"] = await invite(
        client, tokens["owner"], f"cm@{slug}.example.com", "contract_manager"
    )
    tokens["counsel"] = await invite(
        client, tokens["owner"], f"counsel@{slug}.example.com", "counsel"
    )
    return tokens


async def test_a_contract_is_reviewed_only_against_an_approved_playbook_with_cited_findings(
    client: AsyncClient, container: Container
) -> None:
    tokens = await legal_team(client)
    manager, counsel = tokens["manager"], tokens["counsel"]
    document_id = await upload(client, container, manager, CONTRACT, "Vendor MSA")

    proposed = await client.post(
        "/playbooks", json={"name": "Standard MSA", "positions": POSITIONS}, headers=auth(manager)
    )
    assert proposed.status_code == 201, proposed.text
    playbook = proposed.json()
    assert playbook["status"] == "draft" and playbook["version"] == 1
    review_body = {
        "title": "Vendor MSA",
        "playbook_id": playbook["id"],
        "document_ids": [document_id],
    }

    # Not approved: no review. And the author cannot approve their own standard.
    refused = await client.post("/contracts/reviews", json=review_body, headers=auth(manager))
    assert refused.status_code == 409, refused.text
    assert "not approved" in refused.json()["message"]
    self_approval = await client.post(f"/playbooks/{playbook['id']}/approve", headers=auth(manager))
    assert self_approval.status_code == 403, self_approval.text  # managers cannot approve at all

    approved = await client.post(f"/playbooks/{playbook['id']}/approve", headers=auth(counsel))
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    reviewed = await client.post("/contracts/reviews", json=review_body, headers=auth(manager))
    assert reviewed.status_code == 201, reviewed.text
    review: dict[str, Any] = reviewed.json()
    assert review["playbook_version"] == 1
    assert len(review["findings"]) == len(POSITIONS)
    by_clause = {
        p["clause"]: f
        for p in approved.json()["positions"]
        for f in review["findings"]
        if f["position_id"] == p["id"]
    }
    # Every finding about the text cites the text, and only this contract's text.
    for found in review["findings"]:
        if found["finding"] != "missing":
            assert found["evidence"] and all(
                e["document_id"] == document_id for e in found["evidence"]
            )
        else:
            assert found["evidence"] == []
    assert by_clause["Data protection"]["finding"] == "missing"
    assert by_clause["Termination for convenience"]["finding"] == "meets"
    # The critical data-protection gap sends the contract to counsel.
    assert "critical_gap" in review["summary"]["flags"]
    assert review["summary"]["needs_counsel"] is True

    listed = (await client.get("/contracts/reviews", headers=auth(counsel))).json()
    assert [r["id"] for r in listed] == [review["id"]]

    audit = (await client.get("/audit/entries", headers=auth(tokens["owner"]))).json()
    actions = {e["action"] for e in audit}
    assert {"playbook.proposed", "playbook.approved", "contract.reviewed"} <= actions
    verified = await client.post("/audit/verify", headers=auth(tokens["owner"]))
    assert verified.json()["is_intact"] is True


async def test_a_new_version_supersedes_the_approved_one(
    client: AsyncClient, container: Container
) -> None:
    tokens = await legal_team(client)
    manager, counsel = tokens["manager"], tokens["counsel"]
    first = (
        await client.post(
            "/playbooks", json={"name": "NDA", "positions": POSITIONS[:1]}, headers=auth(manager)
        )
    ).json()
    await client.post(f"/playbooks/{first['id']}/approve", headers=auth(counsel))
    second = (
        await client.post(
            "/playbooks", json={"name": "NDA", "positions": POSITIONS[:2]}, headers=auth(manager)
        )
    ).json()
    assert second["version"] == 2
    await client.post(f"/playbooks/{second['id']}/approve", headers=auth(counsel))
    versions = {
        p["version"]: p["status"]
        for p in (await client.get("/playbooks", headers=auth(manager))).json()
        if p["name"] == "NDA"
    }
    assert versions == {1: "superseded", 2: "approved"}


async def test_recruiting_roles_cannot_review_contracts_and_workspaces_stay_apart(
    client: AsyncClient, container: Container
) -> None:
    acme = await legal_team(client, "acme")
    document_id = await upload(client, container, acme["manager"], CONTRACT, "Vendor MSA")
    playbook = (
        await client.post(
            "/playbooks",
            json={"name": "MSA", "positions": POSITIONS},
            headers=auth(acme["manager"]),
        )
    ).json()
    await client.post(f"/playbooks/{playbook['id']}/approve", headers=auth(acme["counsel"]))
    body = {"title": "MSA", "playbook_id": playbook["id"], "document_ids": [document_id]}

    # A recruiter holds no contract permission: the catalogue granted them none.
    as_recruiter = await client.post(
        "/contracts/reviews", json=body, headers=auth(acme["recruiter"])
    )
    assert as_recruiter.status_code == 403, as_recruiter.text

    review = (
        await client.post("/contracts/reviews", json=body, headers=auth(acme["manager"]))
    ).json()

    globex = await legal_team(client, "globex")
    unseen = await client.get(f"/contracts/reviews/{review['id']}", headers=auth(globex["counsel"]))
    assert unseen.status_code == 404, unseen.text
    assert (await client.get("/playbooks", headers=auth(globex["counsel"]))).json() == []
    # Another workspace's document is indistinguishable from one that does not exist.
    foreign = await client.post(
        "/contracts/reviews",
        json={**body, "playbook_id": playbook["id"]},
        headers=auth(globex["manager"]),
    )
    assert foreign.status_code in (404, 422), foreign.text
