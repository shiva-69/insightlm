"""Streamed answers: claims appear as they are validated, and a failed attempt is
withdrawn before its correction streams.

The contract from ADR-0006 must survive streaming unchanged: a finished answer contains
only claims from an attempt in which every claim resolved to a retrieved passage.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from insightlm.bootstrap import Container
from insightlm.retrieval.domain.answer import ProposedClaim
from insightlm.retrieval.domain.evidence import EvidenceBundle
from insightlm.retrieval.ports.generation import (
    ClaimProposed,
    GenerationEvent,
    GenerationFinished,
    GenerationResult,
)
from insightlm.shared.domain.errors import BudgetExceededError
from tests.conftest import requires_docker
from tests.integration.test_recruiting_flow import STRONG_CV, auth, upload, workspace

pytestmark = [pytest.mark.integration, requires_docker]

QUESTION = {"question": "Who has run Kubernetes in production?"}


def events_of(body: str) -> list[tuple[str, Any]]:
    events = []
    for block in body.strip().split("\n\n"):
        fields = dict(line.split(": ", 1) for line in block.splitlines())
        events.append((fields["event"], json.loads(fields["data"])))
    return events


async def test_claims_stream_in_order_and_end_with_the_same_answer_ask_returns(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    recruiter = tokens["recruiter"]
    await upload(client, container, recruiter, STRONG_CV, "Asha CV")

    response = await client.post("/ask/stream", json=QUESTION, headers=auth(recruiter))
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = events_of(response.text)
    kinds = [kind for kind, _ in events]

    assert kinds[0] == "evidence" and kinds[-1] == "done"
    assert set(kinds[1:-1]) <= {"claim"} and "claim" in kinds
    passages = {p["label"] for p in events[0][1]}
    done = events[-1][1]
    streamed = [data for kind, data in events if kind == "claim"]
    assert done["outcome"] == "answered"
    assert streamed == done["claims"]
    assert {label for c in streamed for label in c["citations"]} <= passages

    buffered = (await client.post("/ask", json=QUESTION, headers=auth(recruiter))).json()
    assert buffered["claims"] == done["claims"]
    assert buffered["retrieval_config_hash"] == done["retrieval_config_hash"]


class _CorrectsItself:
    """First attempt: one good claim, then one citing a source that was never retrieved.
    Second attempt: two good claims."""

    def __init__(self) -> None:
        self.attempts = 0

    async def generate(self, *args: object, **kwargs: object) -> GenerationResult:
        raise AssertionError("the streaming path must be used")

    async def stream(
        self, question: str, evidence: EvidenceBundle, *, feedback: str | None = None
    ) -> AsyncIterator[GenerationEvent]:
        self.attempts += 1
        real = evidence.spans[0].label
        if feedback is None:
            claims = [
                ProposedClaim("Ran Kubernetes in production.", (real,)),
                ProposedClaim("Also led a team of forty.", ("C99",)),
            ]
        else:
            assert "C99" in feedback  # the correction names the invented source
            claims = [
                ProposedClaim("Ran Kubernetes in production.", (real,)),
                ProposedClaim("Across three regions.", (real,)),
            ]
        for claim in claims:
            yield ClaimProposed(claim)
        yield GenerationFinished(
            GenerationResult(claims=claims, model_version="scripted", prompt_version="test")
        )


async def test_an_attempt_with_an_unsupported_claim_is_withdrawn_before_its_correction(
    client: AsyncClient, container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = await workspace(client)
    recruiter = tokens["recruiter"]
    await upload(client, container, recruiter, STRONG_CV, "Asha CV")
    scripted = _CorrectsItself()
    monkeypatch.setattr(container.retrieval.ask, "_generator", scripted)

    events = events_of(
        (await client.post("/ask/stream", json=QUESTION, headers=auth(recruiter))).text
    )
    kinds = [kind for kind, _ in events]
    # The good claim from the failed attempt was shown, then withdrawn; the invented one
    # never was. The correction streams afresh.
    assert kinds == ["evidence", "claim", "withdrawn", "claim", "claim", "done"]
    assert scripted.attempts == 2
    done = events[-1][1]
    assert done["outcome"] == "answered"
    assert [c["text"] for c in done["claims"]] == [
        "Ran Kubernetes in production.",
        "Across three regions.",
    ]
    assert all("C99" not in c["citations"] for c in done["claims"])


class _Spent:
    async def charge(self, *args: object, **kwargs: object) -> None:
        raise BudgetExceededError("this workspace has used today's model budget")


async def test_a_refusal_before_the_stream_starts_is_a_plain_http_error(
    client: AsyncClient, container: Container, monkeypatch: pytest.MonkeyPatch
) -> None:
    tokens = await workspace(client)
    monkeypatch.setattr(container.retrieval.ask, "_budget", _Spent())
    response = await client.post("/ask/stream", json=QUESTION, headers=auth(tokens["recruiter"]))
    assert response.status_code == 429, response.text
    assert response.headers["content-type"].startswith("application/json")


async def test_a_streamed_answer_records_its_provenance_once(
    client: AsyncClient,
    container: Container,
    admin_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    tokens = await workspace(client)
    recruiter = tokens["recruiter"]
    await upload(client, container, recruiter, STRONG_CV, "Asha CV")
    # The database is shared across tests; a question only this test asks.
    question = "Which clusters did Asha operate, according to provenance?"
    await client.post("/ask/stream", json={"question": question}, headers=auth(recruiter))
    async with admin_session_factory() as session:
        rows = (
            await session.execute(
                text("SELECT outcome FROM answers WHERE question = :q"), {"q": question}
            )
        ).all()
    assert [r.outcome for r in rows] == ["answered"]
