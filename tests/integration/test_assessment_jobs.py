"""Assessments as jobs: requested in a transaction, run by the worker, recorded either way.

The worker is driven inline here -- the task functions are called directly with a
recording queue standing in for Redis -- so every path a real job takes is exercised
against real Postgres: queued, waiting for the CV, retried, refused, succeeded.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.bootstrap import Container
from insightlm.recruiting.ports.judge import JudgeResult
from insightlm.shared.domain.errors import BudgetExceededError
from insightlm.shared.domain.ids import DocumentVersionId, TenantId
from insightlm.worker import main as worker
from tests.conftest import requires_docker
from tests.integration.test_recruiting_flow import (
    STRONG_CV,
    WEAK_CV,
    add_candidate,
    auth,
    pdf,
    requisition_with_rubric,
    upload,
    workspace,
)
from tests.integration.test_worker_tasks import RecordingQueue

pytestmark = [pytest.mark.integration, requires_docker]


async def approved_requisition(client: AsyncClient, tokens: dict[str, str]) -> str:
    requisition_id, rubric_id = await requisition_with_rubric(client, tokens["recruiter"])
    approved = await client.post(
        f"/rubrics/{rubric_id}/approve", headers=auth(tokens["hiring_manager"])
    )
    assert approved.status_code == 200, approved.text
    return requisition_id


def tenant_of(container: Container, token: str) -> str:
    return str(container.token_issuer.verify_access_token(token).tenant_id)


async def run_job(
    container: Container, tenant_id: str, job_id: str
) -> tuple[dict[str, Any], RecordingQueue]:
    queue = RecordingQueue()
    result = await worker.run_assessment_job(
        {"container": container, "redis": queue}, tenant_id, job_id
    )
    return result, queue


async def job(client: AsyncClient, token: str, job_id: str) -> dict[str, Any]:
    response = await client.get(f"/assessment-jobs/{job_id}", headers=auth(token))
    assert response.status_code == 200, response.text
    body: dict[str, Any] = response.json()
    return body


async def test_a_request_is_queued_at_once_and_the_worker_runs_it(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    recruiter = tokens["recruiter"]
    requisition_id = await approved_requisition(client, tokens)
    document_id = await upload(client, container, recruiter, STRONG_CV, "Asha CV")
    added = await add_candidate(client, recruiter, requisition_id, "Asha", document_id)

    requested = await client.post(
        f"/applications/{added['application']['id']}/assessments", headers=auth(recruiter)
    )
    assert requested.status_code == 202, requested.text
    queued = requested.json()
    assert queued["status"] == "queued" and queued["attempts"] == 0

    # The outbox carries the request to the worker task, as it does an upload.
    queue = RecordingQueue()
    await worker.drain_outbox({"container": container, "redis": queue})
    routed = [args for args, _ in queue.jobs if args[0] == worker.ASSESS_TASK]
    assert routed == [(worker.ASSESS_TASK, tenant_of(container, recruiter), queued["id"])]

    result, _ = await run_job(container, tenant_of(container, recruiter), queued["id"])
    assert result["outcome"] == "succeeded"

    done = await job(client, recruiter, queued["id"])
    assert done["status"] == "succeeded" and done["attempts"] == 1
    assert done["assessment_id"] == result["assessment_id"]

    shortlist = (
        await client.get(f"/requisitions/{requisition_id}/shortlist", headers=auth(recruiter))
    ).json()
    [entry] = shortlist
    assert entry["job"]["status"] == "succeeded"
    assert entry["assessment"]["id"] == result["assessment_id"]
    assert entry["stale"] is False

    # Delivered twice (the reaper, a redelivery): the second run does nothing.
    again, _ = await run_job(container, tenant_of(container, recruiter), queued["id"])
    assert again["outcome"] == "skipped"


async def test_asking_twice_joins_the_queued_job_instead_of_paying_twice(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    recruiter = tokens["recruiter"]
    requisition_id = await approved_requisition(client, tokens)
    document_id = await upload(client, container, recruiter, STRONG_CV, "Asha CV")
    added = await add_candidate(client, recruiter, requisition_id, "Asha", document_id)
    url = f"/applications/{added['application']['id']}/assessments"

    first = (await client.post(url, headers=auth(recruiter))).json()
    second = (await client.post(url, headers=auth(recruiter))).json()
    assert first["id"] == second["id"]


async def test_the_job_waits_for_the_cv_without_spending_an_attempt(
    client: AsyncClient, container: Container
) -> None:
    """Queued the moment a CV is uploaded, before ingestion has read it: the job waits
    rather than judging an empty document and marking every criterion absent."""
    tokens = await workspace(client)
    recruiter = tokens["recruiter"]
    requisition_id = await approved_requisition(client, tokens)
    uploaded = await client.post(
        "/documents",
        files={"file": ("Asha.pdf", pdf(STRONG_CV), "application/pdf")},
        headers=auth(recruiter),
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    added = await add_candidate(client, recruiter, requisition_id, "Asha", body["document"]["id"])
    queued = (
        await client.post(
            f"/applications/{added['application']['id']}/assessments", headers=auth(recruiter)
        )
    ).json()
    tenant = tenant_of(container, recruiter)

    waiting, queue = await run_job(container, tenant, queued["id"])
    assert waiting["outcome"] == "waiting" and waiting["retry_in"] == 10.0
    [(args, kwargs)] = queue.jobs
    assert (
        args == (worker.ASSESS_TASK, tenant, queued["id"])
        and kwargs["_defer_by"].total_seconds() == 10
    )
    state = await job(client, recruiter, queued["id"])
    assert state["status"] == "queued" and state["attempts"] == 0

    outcome = await container.documents.ingestion.run(
        tenant_id=TenantId(UUID(tenant)), version_id=DocumentVersionId(UUID(body["version"]["id"]))
    )
    assert outcome.completed
    finished, _ = await run_job(container, tenant, queued["id"])
    assert finished["outcome"] == "succeeded"


class _Down:
    async def judge(self, *args: object, **kwargs: object) -> JudgeResult:
        raise RuntimeError("provider unavailable")


async def test_a_provider_failure_is_retried_with_backoff_then_recorded(
    client: AsyncClient, container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = await workspace(client)
    recruiter = tokens["recruiter"]
    requisition_id = await approved_requisition(client, tokens)
    document_id = await upload(client, container, recruiter, STRONG_CV, "Asha CV")
    added = await add_candidate(client, recruiter, requisition_id, "Asha", document_id)
    queued = (
        await client.post(
            f"/applications/{added['application']['id']}/assessments", headers=auth(recruiter)
        )
    ).json()
    tenant = tenant_of(container, recruiter)
    monkeypatch.setattr(container.recruiting.run_assessment, "_judge", _Down())

    delays = []
    for _ in range(3):
        result, _queue = await run_job(container, tenant, queued["id"])
        delays.append(result["retry_in"])
    assert delays == [5.0, 10.0, None]
    assert result["outcome"] == "failed"

    failed = await job(client, recruiter, queued["id"])
    assert failed["status"] == "failed" and failed["attempts"] == 3
    assert "provider unavailable" in failed["error"]


class _Spent:
    async def charge(self, *args: object, **kwargs: object) -> None:
        raise BudgetExceededError("this workspace has used today's model budget")


async def test_a_refusal_is_final_and_keeps_its_reason(
    client: AsyncClient, container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running a refused job again would be refused again, so it fails at once."""
    tokens = await workspace(client)
    recruiter = tokens["recruiter"]
    requisition_id = await approved_requisition(client, tokens)
    document_id = await upload(client, container, recruiter, STRONG_CV, "Asha CV")
    added = await add_candidate(client, recruiter, requisition_id, "Asha", document_id)
    url = f"/applications/{added['application']['id']}/assessments"
    monkeypatch.setattr(container.recruiting.run_assessment, "_budget", _Spent())

    queued = (await client.post(url, headers=auth(recruiter))).json()
    result, queue = await run_job(container, tenant_of(container, recruiter), queued["id"])
    assert result["outcome"] == "failed" and result["retry_in"] is None and not queue.jobs
    failed = await job(client, recruiter, queued["id"])
    assert failed["attempts"] == 1 and "budget" in failed["error"]

    # Waiting inline, the caller hears the real refusal, not a generic failure.
    waited = await client.post(f"{url}?wait=true", headers=auth(recruiter))
    assert waited.status_code == 429, waited.text


async def test_a_batch_queues_the_unassessed_then_whatever_a_new_rubric_made_stale(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    recruiter, manager = tokens["recruiter"], tokens["hiring_manager"]
    requisition_id = await approved_requisition(client, tokens)
    apps = []
    for name, cv in (("Asha", STRONG_CV), ("Ben", WEAK_CV), ("Cara", STRONG_CV)):
        document_id = await upload(client, container, recruiter, cv, f"{name} CV")
        apps.append(
            (await add_candidate(client, recruiter, requisition_id, name, document_id))[
                "application"
            ]["id"]
        )
    asha, ben, cara = apps
    assessed = await client.post(
        f"/applications/{asha}/assessments?wait=true", headers=auth(recruiter)
    )
    assert assessed.status_code == 201, assessed.text
    rejected = await client.post(
        f"/applications/{cara}/stage",
        json={"to": "rejected", "reason": "skills_gap"},
        headers=auth(recruiter),
    )
    assert rejected.status_code == 200, rejected.text

    batch_url = f"/requisitions/{requisition_id}/assessments"
    unassessed = (
        await client.post(batch_url, json={"scope": "unassessed"}, headers=auth(recruiter))
    ).json()
    assert [j["application_id"] for j in unassessed["queued"]] == [ben]
    assert unassessed["skipped_current"] == 1 and unassessed["skipped_closed"] == 1
    tenant = tenant_of(container, recruiter)
    await run_job(container, tenant, unassessed["queued"][0]["id"])

    # The bar moves: a new version is proposed and approved. Both scores now rest on
    # criteria no longer in force, and the shortlist says so.
    proposed = await client.post(
        f"/requisitions/{requisition_id}/rubrics",
        json={
            "criteria": [{"label": "Python backend services", "kind": "must_have", "weight": 1.0}]
        },
        headers=auth(recruiter),
    )
    assert proposed.status_code == 201, proposed.text
    approved = await client.post(f"/rubrics/{proposed.json()['id']}/approve", headers=auth(manager))
    assert approved.status_code == 200, approved.text
    shortlist = (
        await client.get(f"/requisitions/{requisition_id}/shortlist", headers=auth(recruiter))
    ).json()
    assert {e["application"]["id"] for e in shortlist if e["stale"]} == {asha, ben}

    stale = (await client.post(batch_url, json={"scope": "stale"}, headers=auth(recruiter))).json()
    assert {j["application_id"] for j in stale["queued"]} == {asha, ben}
    for queued in stale["queued"]:
        result, _ = await run_job(container, tenant, queued["id"])
        assert result["outcome"] == "succeeded"
    shortlist = (
        await client.get(f"/requisitions/{requisition_id}/shortlist", headers=auth(recruiter))
    ).json()
    assert not any(e["stale"] for e in shortlist)


async def test_an_interviewer_cannot_queue_assessments(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    requisition_id = await approved_requisition(client, tokens)
    invite = await client.post(
        "/members/invitations",
        json={"email": "ivr@acme.example.com", "role": "interviewer"},
        headers=auth(tokens["owner"]),
    )
    await client.post(
        "/auth/invitations/accept",
        json={
            "token": invite.json()["token"],
            "display_name": "Ivr",
            "password": "correct-horse-battery-staple",
        },
    )
    login = await client.post(
        "/auth/login",
        json={"email": "ivr@acme.example.com", "password": "correct-horse-battery-staple"},
    )
    interviewer = login.json()["access_token"]
    refused = await client.post(
        f"/requisitions/{requisition_id}/assessments",
        json={"scope": "all"},
        headers=auth(interviewer),
    )
    assert refused.status_code == 403, refused.text


async def test_the_reaper_puts_an_abandoned_job_back_in_the_queue(
    client: AsyncClient,
    container: Container,
    admin_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A worker that dies mid-assessment leaves a RUNNING row that nothing would ever
    finish. After fifteen minutes the reaper returns it to the queue."""
    tokens = await workspace(client)
    recruiter = tokens["recruiter"]
    requisition_id = await approved_requisition(client, tokens)
    document_id = await upload(client, container, recruiter, STRONG_CV, "Asha CV")
    added = await add_candidate(client, recruiter, requisition_id, "Asha", document_id)
    queued = (
        await client.post(
            f"/applications/{added['application']['id']}/assessments", headers=auth(recruiter)
        )
    ).json()
    async with admin_session_factory() as session:
        await session.execute(
            text(
                "UPDATE assessment_jobs SET status = 'running', attempts = 1, "
                "updated_at = now() - interval '20 minutes' WHERE id = :id"
            ),
            {"id": queued["id"]},
        )
        await session.commit()

    queue = RecordingQueue()
    reaped = await worker.requeue_stalled(
        {"container": container, "redis": queue, "admin_sessions": admin_session_factory}
    )
    assert reaped >= 1
    assert (worker.ASSESS_TASK, tenant_of(container, recruiter), queued["id"]) in [
        a for a, _ in queue.jobs
    ]
    assert (await job(client, recruiter, queued["id"]))["status"] == "queued"
    finished, _ = await run_job(container, tenant_of(container, recruiter), queued["id"])
    assert finished["outcome"] == "succeeded"
