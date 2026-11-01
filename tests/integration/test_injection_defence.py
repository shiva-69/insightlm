"""The injection defence through the real pipeline: upload, ingest, index, retrieve."""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient

from insightlm.bootstrap import Container, build_role_catalogue
from insightlm.evaluation.adapters.redteam import CLAIM, build_redteam_v1
from insightlm.recruiting.domain.roles import RecruitingRole
from insightlm.retrieval.application.answer import AnswerRequest
from insightlm.shared.domain.ids import DocumentVersionId
from insightlm.shared.domain.policy import Actor
from tests.conftest import requires_docker
from tests.integration.test_recruiting_flow import auth, workspace

pytestmark = [pytest.mark.integration, requires_docker]


async def test_hidden_text_is_reported_and_never_indexed(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    attack = next(d for d in build_redteam_v1() if d.id == "a-white-claim")

    uploaded = await client.post(
        "/documents",
        files={"file": ("cv.pdf", attack.pdf, "application/pdf")},
        data={"title": "Jordan Ellis CV"},
        headers=auth(tokens["recruiter"]),
    )
    assert uploaded.status_code == 201, uploaded.text
    body = uploaded.json()
    claims = container.token_issuer.verify_access_token(tokens["recruiter"])
    outcome = await container.documents.ingestion.run(
        tenant_id=claims.tenant_id, version_id=DocumentVersionId(UUID(body["version"]["id"]))
    )
    assert outcome.completed, outcome.error

    findings = (
        await client.get(
            f"/documents/{body['document']['id']}/findings", headers=auth(tokens["recruiter"])
        )
    ).json()
    hidden = [f for f in findings if f["kind"] == "low_contrast_text"]
    assert hidden and all(f["excluded"] for f in hidden)
    assert "Kubernetes" in " ".join(f["text"] for f in hidden)
    assert hidden[0]["rect"] is not None, "the reviewer needs to see where it was"

    # And the payload can't be retrieved: asking for it finds only the real CV.
    actor = Actor.of(
        build_role_catalogue(),
        user_id=claims.user_id,
        tenant_id=claims.tenant_id,
        role=RecruitingRole.RECRUITER,
    )
    evidence = await container.retrieval.ask.retrieve(
        actor, AnswerRequest(question="Kubernetes in production payment processing")
    )
    retrieved = " ".join(span.text for span in evidence.spans)
    assert retrieved, "the honest CV text should still be retrievable"
    assert "Kubernetes" not in retrieved
    assert CLAIM[:30] not in retrieved
