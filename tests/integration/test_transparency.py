"""The subject-access record, and retention through verified erasure."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.bootstrap import Container
from tests.conftest import requires_docker
from tests.integration.test_recruiting_flow import (
    STRONG_CV,
    WEAK_CV,
    add_candidate,
    auth,
    requisition_with_rubric,
    upload,
    workspace,
)

pytestmark = [pytest.mark.integration, requires_docker]


async def test_the_record_holds_everything_about_the_person(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    recruiter, owner = tokens["recruiter"], tokens["owner"]
    document_id = await upload(client, container, recruiter, STRONG_CV, "Asha Rao CV")
    requisition_id, rubric_id = await requisition_with_rubric(client, recruiter)
    await client.post(f"/rubrics/{rubric_id}/approve", headers=auth(tokens["hiring_manager"]))
    added = await add_candidate(client, recruiter, requisition_id, "Asha Rao", document_id)
    app_id = added["application"]["id"]
    await client.post(f"/applications/{app_id}/assessments", headers=auth(recruiter))
    await client.post(
        f"/applications/{app_id}/stage", json={"to": "screened"}, headers=auth(recruiter)
    )
    await client.put(
        f"/candidates/{added['candidate']['id']}/self-identification",
        json={"dimension": "gender", "value": "woman"},
        headers=auth(owner),
    )

    record = (
        await client.get(f"/candidates/{added['candidate']['id']}/record", headers=auth(owner))
    ).json()
    assert record["candidate"]["name"] == "Asha Rao"
    assert [d["title"] for d in record["documents"]] == ["Asha Rao CV"]
    [application] = record["applications"]
    assert application["application"]["stage"] == "screened"
    assert application["assessment"]["judgments"], "every verdict, with its evidence"
    assert [t["to_stage"] for t in application["transitions"]] == ["screened"]
    assert record["self_identification"] == {"gender": "woman"}

    denied = await client.get(
        f"/candidates/{added['candidate']['id']}/record", headers=auth(recruiter)
    )
    assert denied.status_code == 403


async def test_retention_erases_only_rejected_candidates_past_the_period(
    client: AsyncClient,
    container: Container,
    admin_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tokens = await workspace(client)
    recruiter, owner = tokens["recruiter"], tokens["owner"]
    requisition_id, _ = await requisition_with_rubric(client, recruiter)
    old = await add_candidate(
        client,
        recruiter,
        requisition_id,
        "Old Rejected",
        await upload(client, container, recruiter, WEAK_CV, "old"),
    )
    active = await add_candidate(
        client,
        recruiter,
        requisition_id,
        "Still Active",
        await upload(client, container, recruiter, STRONG_CV, "active"),
    )
    await client.post(
        f"/applications/{old['application']['id']}/stage",
        json={"to": "rejected", "reason": "skills_gap"},
        headers=auth(recruiter),
    )

    # Two years pass for the rejected candidate. (The privileged session stands in for
    # time; the application code is untouched.)
    async with admin_session_factory() as session:
        await session.begin()
        for sql in (
            "UPDATE candidates SET created_at = created_at - interval '730 days' WHERE id = :c",
            "UPDATE applications SET created_at = created_at - interval '730 days', "
            "stage_entered_at = stage_entered_at - interval '730 days' WHERE candidate_id = :c",
            "UPDATE stage_transitions SET at = at - interval '730 days' WHERE application_id IN "
            "(SELECT id FROM applications WHERE candidate_id = :c)",
        ):
            await session.execute(text(sql), {"c": UUID(old["candidate"]["id"])})
        await session.commit()

    dry = (await client.post("/governance/retention/run", headers=auth(owner))).json()
    assert [e["candidate_id"] for e in dry["expired"]] == [old["candidate"]["id"]]
    assert dry["erased"] == []

    ran = (await client.post("/governance/retention/run?dry_run=false", headers=auth(owner))).json()
    [report] = ran["erased"]
    assert report["verified"] is True, report["residue"]

    shortlist = (
        await client.get(f"/requisitions/{requisition_id}/shortlist", headers=auth(owner))
    ).json()
    assert [e["candidate"]["id"] for e in shortlist] == [active["candidate"]["id"]]
