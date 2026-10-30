"""The Phase 5 exit criterion: several people, several roles, correct and different views.

One application goes from applied to hired while a recruiter, two interviewers and a
coordinator each see only what their role allows. The gates hold at every step, and
anchoring protection keeps the first interviewer's view from reaching the second.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from httpx import AsyncClient

from insightlm.bootstrap import Container
from tests.conftest import requires_docker
from tests.integration.test_recruiting_flow import (
    PASSWORD,
    STRONG_CV,
    WEAK_CV,
    add_candidate,
    auth,
    login,
    requisition_with_rubric,
    upload,
    workspace,
)

pytestmark = [pytest.mark.integration, requires_docker]


async def invite(client: AsyncClient, owner: str, role: str, name: str) -> tuple[str, str]:
    """A new member with the role; returns (token, user id)."""
    email = f"{name}@acme.example.com"
    invitation = await client.post(
        "/members/invitations", json={"email": email, "role": role}, headers=auth(owner)
    )
    assert invitation.status_code == 201, invitation.text
    accepted = await client.post(
        "/auth/invitations/accept",
        json={"token": invitation.json()["token"], "display_name": name, "password": PASSWORD},
    )
    assert accepted.status_code == 201, accepted.text
    return await login(client, email), accepted.json()["user"]["id"]


async def ready_application(
    client: AsyncClient, container: Container, tokens: dict[str, str], lines: list[str], name: str
) -> tuple[str, str]:
    """An assessed application on a requisition with an approved rubric."""
    recruiter = tokens["recruiter"]
    document_id = await upload(client, container, recruiter, lines, f"{name} CV")
    requisition_id, rubric_id = await requisition_with_rubric(client, recruiter)
    approved = await client.post(
        f"/rubrics/{rubric_id}/approve", headers=auth(tokens["hiring_manager"])
    )
    assert approved.status_code == 200, approved.text
    added = await add_candidate(client, recruiter, requisition_id, name, document_id)
    application_id: str = added["application"]["id"]
    assessed = await client.post(
        f"/applications/{application_id}/assessments?wait=true", headers=auth(recruiter)
    )
    assert assessed.status_code == 201, assessed.text
    return requisition_id, application_id


async def move(client: AsyncClient, token: str, application_id: str, to: str, **extra: Any) -> Any:
    return await client.post(
        f"/applications/{application_id}/stage", json={"to": to, **extra}, headers=auth(token)
    )


async def submit(client: AsyncClient, token: str, detail: dict[str, Any], verdict: str) -> Any:
    card = detail["scorecards"]["own"]
    criteria = detail["rubric"]["criteria"]
    return await client.post(
        f"/scorecards/{card['id']}/submit",
        json={
            "ratings": [{"criterion_id": c["id"], "rating": 3} for c in criteria],
            "recommendation": verdict,
            "summary": "Clear, specific answers on settlement failures.",
        },
        headers=auth(token),
    )


async def view(client: AsyncClient, token: str, application_id: str) -> Any:
    return await client.get(f"/applications/{application_id}", headers=auth(token))


async def test_applied_to_hired_with_every_gate_and_every_view(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    recruiter, owner = tokens["recruiter"], tokens["owner"]
    ada, ada_id = await invite(client, owner, "interviewer", "ada")
    lin, lin_id = await invite(client, owner, "interviewer", "lin")
    _, coordinator_id = await invite(client, owner, "coordinator", "cora")

    requisition_id, application_id = await ready_application(
        client, container, tokens, STRONG_CV, "Asha"
    )
    other_requisition, _ = await requisition_with_rubric(client, recruiter)

    # -- stages and gates ------------------------------------------------------------
    skipped = await move(client, recruiter, application_id, "interview")
    assert skipped.status_code == 409, skipped.text
    assert (await move(client, recruiter, application_id, "screened")).status_code == 200
    assert (await move(client, recruiter, application_id, "interview")).status_code == 200

    early = await move(client, recruiter, application_id, "offer")
    assert early.status_code == 409
    assert "2 more submitted scorecards" in early.json()["message"]

    detail = (await view(client, recruiter, application_id)).json()
    assert detail["next_stage"] == "offer"
    assert detail["next_blockers"] == [
        "2 more submitted scorecards needed before an offer (0 of 2)"
    ]

    # -- the panel -------------------------------------------------------------------
    for user_id in (ada_id, lin_id):
        added = await client.post(
            f"/applications/{application_id}/panel",
            json={"user_id": user_id},
            headers=auth(recruiter),
        )
        assert added.status_code == 201, added.text
    twice = await client.post(
        f"/applications/{application_id}/panel", json={"user_id": ada_id}, headers=auth(recruiter)
    )
    assert twice.status_code == 409
    for not_an_interviewer in (coordinator_id, str(uuid4())):
        refused = await client.post(
            f"/applications/{application_id}/panel",
            json={"user_id": not_an_interviewer},
            headers=auth(recruiter),
        )
        assert refused.status_code == 422, refused.text

    # -- an interviewer's view is scoped and score-free ------------------------------
    listed = (await client.get("/requisitions", headers=auth(ada))).json()
    assert [r["id"] for r in listed] == [requisition_id]
    hidden = await client.get(f"/requisitions/{other_requisition}", headers=auth(ada))
    assert hidden.status_code == 404

    ada_view = (await view(client, ada, application_id)).json()
    assert ada_view["assessment"] is None, "interviewers do not see the AI score"
    assert ada_view["scorecards"]["own"]["is_submitted"] is False
    assert ada_view["scorecards"]["others"] == []

    interviews = (await client.get("/me/interviews", headers=auth(ada))).json()
    assert [i["application"]["id"] for i in interviews] == [application_id]

    forbidden = await move(client, ada, application_id, "offer")
    assert forbidden.status_code == 403

    # -- anchoring protection --------------------------------------------------------
    assert (await submit(client, ada, ada_view, "strong_yes")).status_code == 200

    lin_view = (await view(client, lin, application_id)).json()
    assert lin_view["scorecards"]["others"] == [], "Lin must not see Ada's view yet"
    assert lin_view["scorecards"]["withheld"] == 1

    assert (await submit(client, lin, lin_view, "yes")).status_code == 200
    lin_after = (await view(client, lin, application_id)).json()
    assert [c["recommendation"] for c in lin_after["scorecards"]["others"]] == ["strong_yes"]

    again = await submit(client, ada, ada_view, "no")
    assert again.status_code == 409, "a submitted scorecard is final"

    # -- through the offer gate ------------------------------------------------------
    assert (await move(client, recruiter, application_id, "offer")).status_code == 200
    assert (await move(client, recruiter, application_id, "hired")).status_code == 200

    final = (await view(client, recruiter, application_id)).json()
    assert [(t["from_stage"], t["to_stage"]) for t in final["transitions"]] == [
        ("applied", "screened"),
        ("screened", "interview"),
        ("interview", "offer"),
        ("offer", "hired"),
    ]
    assert final["next_stage"] is None
    assert len(final["scorecards"]["others"]) == 2

    actions = [
        e["action"]
        for e in (await client.get("/audit/entries?limit=200", headers=auth(owner))).json()
    ]
    assert actions.count("application.stage_transitioned") == 4
    assert actions.count("scorecard.submitted") == 2
    assert actions.count("application.interviewer_assigned") == 2
    assert (await client.post("/audit/verify", headers=auth(owner))).json()["is_intact"] is True


async def test_rejection_needs_a_reason_and_records_it(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    _, application_id = await ready_application(client, container, tokens, WEAK_CV, "Ben")

    bare = await move(client, tokens["recruiter"], application_id, "rejected")
    assert bare.status_code == 422, bare.text

    rejected = await move(
        client,
        tokens["recruiter"],
        application_id,
        "rejected",
        reason="skills_gap",
        note="No backend work.",
    )
    assert rejected.status_code == 200, rejected.text
    history = (await view(client, tokens["recruiter"], application_id)).json()["transitions"]
    assert history[-1]["reason"] == "skills_gap"
    assert history[-1]["note"] == "No backend work."


async def test_an_unassigned_interviewer_cannot_see_the_application(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    outsider, _ = await invite(client, tokens["owner"], "interviewer", "otto")
    _, application_id = await ready_application(client, container, tokens, STRONG_CV, "Asha")

    assert (await view(client, outsider, application_id)).status_code == 404
    assert (await client.get("/requisitions", headers=auth(outsider))).json() == []


async def test_talent_search_returns_people_with_their_passages(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    recruiter = tokens["recruiter"]
    asha_doc = await upload(client, container, recruiter, STRONG_CV, "Asha CV")
    ben_doc = await upload(client, container, recruiter, WEAK_CV, "Ben CV")
    requisition_id, _ = await requisition_with_rubric(client, recruiter)
    await add_candidate(client, recruiter, requisition_id, "Asha", asha_doc)
    await add_candidate(client, recruiter, requisition_id, "Ben", ben_doc)

    response = await client.get(
        "/talent/search", params={"q": "Kubernetes clusters in production"}, headers=auth(recruiter)
    )
    assert response.status_code == 200, response.text
    matches = response.json()
    assert matches[0]["candidate"]["name"] == "Asha"
    # Each person's passages come from their own documents.
    for match in matches:
        own = set(match["candidate"]["document_ids"])
        assert match["passages"]
        assert all(p["document_id"] in own for p in match["passages"])

    interviewer, _ = await invite(client, tokens["owner"], "interviewer", "ivy")
    blocked = await client.get("/talent/search", params={"q": "Python"}, headers=auth(interviewer))
    assert blocked.status_code == 403
