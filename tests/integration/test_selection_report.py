"""Selection rates end to end, and who may see them."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from insightlm.bootstrap import Container
from tests.conftest import requires_docker
from tests.integration.test_pipeline_flow import invite
from tests.integration.test_recruiting_flow import (
    STRONG_CV,
    add_candidate,
    auth,
    requisition_with_rubric,
    upload,
    workspace,
)

pytestmark = [pytest.mark.integration, requires_docker]


async def test_the_report_counts_screening_decisions_by_self_identified_group(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    recruiter, owner = tokens["recruiter"], tokens["owner"]
    requisition_id, rubric_id = await requisition_with_rubric(client, recruiter)
    await client.post(f"/rubrics/{rubric_id}/approve", headers=auth(tokens["hiring_manager"]))
    document_id = await upload(client, container, recruiter, STRONG_CV, "CV")

    # 5 men: 4 advanced. 5 women: 2 advanced. 1 pending, 1 undisclosed rejected.
    plan = [("man", True)] * 4 + [("man", False)] + [("woman", True)] * 2 + [("woman", False)] * 3
    plan += [("woman", None), ("prefer_not_to_say", False)]
    for i, (value, advanced) in enumerate(plan):
        added = await add_candidate(client, recruiter, requisition_id, f"Person {i}", document_id)
        app_id = added["application"]["id"]
        put = await client.put(
            f"/candidates/{added['candidate']['id']}/self-identification",
            json={"dimension": "gender", "value": value},
            headers=auth(owner),
        )
        assert put.status_code == 204, put.text
        if advanced is True:
            await client.post(f"/applications/{app_id}/assessments", headers=auth(recruiter))
            moved = await client.post(
                f"/applications/{app_id}/stage", json={"to": "screened"}, headers=auth(recruiter)
            )
            assert moved.status_code == 200, moved.text
        elif advanced is False:
            await client.post(
                f"/applications/{app_id}/stage",
                json={"to": "rejected", "reason": "skills_gap"},
                headers=auth(recruiter),
            )

    report = (
        await client.get(
            f"/governance/selection-rates?requisition_id={requisition_id}", headers=auth(owner)
        )
    ).json()
    groups = {g["group"]: g for g in report["groups"]}
    assert (groups["man"]["decided"], groups["man"]["advanced"]) == (5, 4)
    assert (groups["woman"]["decided"], groups["woman"]["advanced"]) == (5, 2)
    assert groups["woman"]["impact_ratio"] == pytest.approx(0.5)
    assert groups["woman"]["adverse"] is True
    assert groups["undisclosed"]["impact_ratio"] is None
    assert report["pending"] == 1
    assert report["rejection_reasons"]["woman"] == {"skills_gap": 3}

    # An auditor can read it; a recruiter can neither read it nor record it.
    auditor, _ = await invite(client, owner, "auditor", "audra")
    assert (
        await client.get("/governance/selection-rates", headers=auth(auditor))
    ).status_code == 200
    assert (
        await client.get("/governance/selection-rates", headers=auth(recruiter))
    ).status_code == 403
    denied = await client.put(
        f"/candidates/{added['candidate']['id']}/self-identification",
        json={"dimension": "gender", "value": "man"},
        headers=auth(recruiter),
    )
    assert denied.status_code == 403
