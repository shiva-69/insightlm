"""Blind screening through the real assessment: what the judge saw, and what it cited."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from insightlm.bootstrap import Container
from insightlm.recruiting.adapters.judges import OfflineCriterionJudge
from insightlm.recruiting.domain.rubric import Criterion
from insightlm.recruiting.ports.judge import JudgeResult
from insightlm.retrieval.domain.evidence import EvidenceBundle
from tests.conftest import requires_docker
from tests.integration.test_recruiting_flow import (
    add_candidate,
    auth,
    upload,
    workspace,
)

pytestmark = [pytest.mark.integration, requires_docker]

CV = [
    "Asha Rao",
    "asha.rao@example.com  +91 98765 43210",
    "EXPERIENCE",
    "Built settlement and reconciliation services in Python and PostgreSQL.",
    "Operated Kubernetes clusters in production; she led the migration.",
    "EDUCATION",
    "B.Tech Computer Science, IIT Bombay, 2019.",
]


class SpyJudge:
    """Records the evidence exactly as the model would have received it."""

    def __init__(self) -> None:
        self.seen: list[str] = []
        self._inner = OfflineCriterionJudge()

    async def judge(self, criterion: Criterion, evidence: EvidenceBundle) -> JudgeResult:
        self.seen.append(evidence.as_context())
        return await self._inner.judge(criterion, evidence)


async def _assess(
    client: AsyncClient, container: Container, *, blind: bool
) -> tuple[SpyJudge, dict]:  # type: ignore[type-arg]
    tokens = await workspace(client)
    recruiter = tokens["recruiter"]
    document_id = await upload(client, container, recruiter, CV, "Asha Rao CV")
    created = await client.post(
        "/requisitions",
        json={"title": "Payments Engineer", "blind_screening": blind},
        headers=auth(recruiter),
    )
    requisition_id = created.json()["id"]
    assert created.json()["blind_screening"] is blind
    # Propose onto *this* requisition and approve it.
    proposed = await client.post(
        f"/requisitions/{requisition_id}/rubrics",
        json={
            "criteria": [
                {"label": "Python backend services", "kind": "must_have", "weight": 1.0},
                {"label": "Kubernetes in production", "kind": "nice_to_have", "weight": 0.5},
            ]
        },
        headers=auth(recruiter),
    )
    await client.post(
        f"/rubrics/{proposed.json()['id']}/approve", headers=auth(tokens["hiring_manager"])
    )
    added = await add_candidate(client, recruiter, requisition_id, "Asha Rao", document_id)

    spy = SpyJudge()
    use_case = container.run_assessment
    original_judge = use_case._judge
    use_case._judge = spy  # the container is frozen; the use case's port is swappable
    try:
        response = await client.post(
            f"/applications/{added['application']['id']}/assessments", headers=auth(recruiter)
        )
    finally:
        use_case._judge = original_judge
    assert response.status_code == 201, response.text
    return spy, response.json()


async def test_a_blind_judge_never_sees_who_the_candidate_is(
    client: AsyncClient, container: Container
) -> None:
    spy, assessment = await _assess(client, container, blind=True)
    seen = "\n".join(spy.seen)
    assert seen, "the judge was called"
    for leaked in ("Asha", "Rao", "example.com", "98765", "IIT Bombay", " she "):
        assert leaked not in seen, leaked
    assert "Kubernetes" in seen and "Python" in seen
    assert assessment["redaction_profile"] == "blind-v1"

    # The reviewer's evidence is the original text, not the redacted copy.
    quotes = " ".join(e["quoted_text"] for j in assessment["judgments"] for e in j["evidence"])
    assert "[candidate]" not in quotes and "[institution]" not in quotes


async def test_blind_screening_can_be_turned_off_per_requisition(
    client: AsyncClient, container: Container
) -> None:
    spy, assessment = await _assess(client, container, blind=False)
    assert "Asha" in "\n".join(spy.seen)
    assert assessment["redaction_profile"] == "none"
