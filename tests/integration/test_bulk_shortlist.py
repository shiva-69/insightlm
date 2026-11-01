"""Phase 4's exit check: 50 CVs in, an evidence-linked ranked shortlist out.

The cohort's true strength is known in advance, so the shortlist can be checked, not
admired: its order should agree with the truth. Uses the offline judge -- free and
deterministic -- so the bar is what the baseline can meet, not what a model can.
"""

from __future__ import annotations

import statistics
from uuid import UUID

import pytest
from httpx import AsyncClient

from insightlm.bootstrap import Container
from insightlm.evaluation.adapters.cohort import build_cohort
from insightlm.shared.domain.ids import DocumentVersionId
from tests.conftest import requires_docker
from tests.integration.test_recruiting_flow import auth, workspace

pytestmark = [pytest.mark.integration, requires_docker]

RUBRIC = [
    {"label": "Python backend services", "kind": "must_have", "weight": 1.0},
    {"label": "Payment processing", "kind": "strong_preference", "weight": 0.7},
    {"label": "Kubernetes in production", "kind": "nice_to_have", "weight": 0.3},
]


def _ranks(values: list[float]) -> list[float]:
    order = sorted(range(len(values)), key=values.__getitem__)
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2
        i = j + 1
    return ranks


def spearman(a: list[float], b: list[float]) -> float:
    return statistics.correlation(_ranks(a), _ranks(b))


async def test_fifty_cvs_in_a_ranked_shortlist_out(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    recruiter, manager = tokens["recruiter"], tokens["hiring_manager"]
    tenant = container.token_issuer.verify_access_token(recruiter).tenant_id

    requisition = (
        await client.post(
            "/requisitions", json={"title": "Payments Engineer"}, headers=auth(recruiter)
        )
    ).json()
    rubric = (
        await client.post(
            f"/requisitions/{requisition['id']}/rubrics",
            json={"criteria": RUBRIC},
            headers=auth(recruiter),
        )
    ).json()
    await client.post(f"/rubrics/{rubric['id']}/approve", headers=auth(manager))

    cohort = build_cohort(50)
    truth: dict[str, float] = {}
    for applicant in cohort:
        uploaded = (
            await client.post(
                "/documents",
                files={"file": (applicant.filename, applicant.pdf, "application/pdf")},
                headers=auth(recruiter),
            )
        ).json()
        outcome = await container.documents.ingestion.run(
            tenant_id=tenant, version_id=DocumentVersionId(UUID(uploaded["version"]["id"]))
        )
        assert outcome.completed, outcome.error
        added = (
            await client.post(
                f"/requisitions/{requisition['id']}/candidates",
                json={"name": applicant.name, "document_ids": [uploaded["document"]["id"]]},
                headers=auth(recruiter),
            )
        ).json()
        assessed = await client.post(
            f"/applications/{added['application']['id']}/assessments?wait=true",
            headers=auth(recruiter),
        )
        assert assessed.status_code == 201, assessed.text
        truth[applicant.name] = applicant.true_score

    shortlist = (
        await client.get(f"/requisitions/{requisition['id']}/shortlist", headers=auth(recruiter))
    ).json()
    assert len(shortlist) == 50
    assert all(e["assessment"] for e in shortlist)

    # Ranked by score, every entry evidence-linked.
    scores = [e["assessment"]["score"] for e in shortlist]
    assert scores == sorted(scores, reverse=True)
    for entry in shortlist:
        for judgment in entry["assessment"]["judgments"]:
            assert judgment["verdict"] == "absent" or judgment["evidence"]

    # And the order agrees with the truth.
    names = [e["candidate"]["name"] for e in shortlist]
    rho = spearman([truth[n] for n in names], scores)
    assert rho > 0.8, f"shortlist order disagrees with the known truth (rho={rho:.2f})"

    # A missing must-have is flagged for a person, never silently ranked away (ADR-0011):
    # every applicant whose CV lacks it carries the flag, and nobody who has it does.
    has_python = {a.name: a.has[0] for a in cohort}
    for entry in shortlist:
        flagged = "missing_must_have" in entry["assessment"]["flags"]
        assert flagged == (not has_python[entry["candidate"]["name"]]), entry["candidate"]["name"]
