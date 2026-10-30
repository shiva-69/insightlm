"""Rate limits, idempotency keys and the model budget, through the real app."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import replace

import pytest
from httpx import ASGITransport, AsyncClient

from insightlm.api.app import create_app
from insightlm.bootstrap import Container
from insightlm.shared.adapters.limits import MemorySpendMeter, MeteredBudget
from tests.conftest import requires_docker
from tests.integration.test_blind_screening import SpyJudge
from tests.integration.test_recruiting_flow import (
    STRONG_CV,
    add_candidate,
    auth,
    requisition_with_rubric,
    upload,
    workspace,
)

pytestmark = [pytest.mark.integration, requires_docker]


async def _client(container: Container, *, trust_proxy: bool) -> AsyncIterator[AsyncClient]:
    limited = replace(
        container,
        settings=container.settings.model_copy(
            update={"rate_limits_enabled": True, "trust_proxy_client_ip": trust_proxy}
        ),
    )
    app = create_app(limited.settings, container=limited)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        app.state.container = limited
        yield client


@pytest.fixture
async def behind_proxy(container: Container) -> AsyncIterator[AsyncClient]:
    async for client in _client(container, trust_proxy=True):
        yield client


@pytest.fixture
async def exposed(container: Container) -> AsyncIterator[AsyncClient]:
    async for client in _client(container, trust_proxy=False):
        yield client


async def _attempts(client: AsyncClient, n: int, ip: str) -> list[int]:
    return [
        (
            await client.post(
                "/auth/login",
                json={"email": "nobody@example.com", "password": "wrong-password-123"},
                headers={"x-insightlm-client-ip": ip},
            )
        ).status_code
        for _ in range(n)
    ]


async def test_sign_in_attempts_are_limited_per_client(behind_proxy: AsyncClient) -> None:
    codes = await _attempts(behind_proxy, 11, "203.0.113.7")
    assert codes[:10] == [401] * 10
    assert codes[10] == 429

    blocked = await behind_proxy.post(
        "/auth/login",
        json={"email": "nobody@example.com", "password": "wrong-password-123"},
        headers={"x-insightlm-client-ip": "203.0.113.7"},
    )
    assert blocked.json()["code"] == "rate_limited"
    assert int(blocked.headers["retry-after"]) >= 1

    # Someone else, behind the same proxy, is unaffected.
    assert await _attempts(behind_proxy, 1, "198.51.100.4") == [401]


async def test_a_forged_client_ip_header_is_ignored_when_the_proxy_isnt_trusted(
    exposed: AsyncClient,
) -> None:
    # Rotating the header must not buy fresh attempts: the socket address is used.
    codes = [(await _attempts(exposed, 1, f"198.51.100.{i}"))[0] for i in range(11)]
    assert codes[-1] == 429


async def test_a_retried_write_with_the_same_key_happens_once(
    behind_proxy: AsyncClient,
) -> None:
    tokens = await workspace(behind_proxy)
    headers = {**auth(tokens["recruiter"]), "Idempotency-Key": "open-req-1"}
    body = {"title": "Payments Engineer", "description": ""}

    first = await behind_proxy.post("/requisitions", json=body, headers=headers)
    retry = await behind_proxy.post("/requisitions", json=body, headers=headers)
    assert first.status_code == retry.status_code == 201
    assert retry.json()["id"] == first.json()["id"]
    assert retry.headers.get("idempotent-replayed") == "true"

    listed = await behind_proxy.get("/requisitions", headers=auth(tokens["recruiter"]))
    assert len(listed.json()) == 1

    reused = await behind_proxy.post(
        "/requisitions", json={**body, "title": "Something else"}, headers=headers
    )
    assert reused.status_code == 422
    assert reused.json()["code"] == "idempotency_mismatch"


async def test_over_budget_assessment_is_refused_before_any_model_call(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    recruiter = tokens["recruiter"]
    document_id = await upload(client, container, recruiter, STRONG_CV, "CV")
    requisition_id, rubric_id = await requisition_with_rubric(client, recruiter)  # 3 criteria
    await client.post(f"/rubrics/{rubric_id}/approve", headers=auth(tokens["hiring_manager"]))
    added = await add_candidate(client, recruiter, requisition_id, "Asha", document_id)

    use_case = container.recruiting.run_assessment
    spy, original_judge, original_budget = SpyJudge(), use_case._judge, use_case._budget
    use_case._judge = spy
    use_case._budget = MeteredBudget(MemorySpendMeter(), calls_per_day=2)
    try:
        response = await client.post(
            f"/applications/{added['application']['id']}/assessments?wait=true",
            headers=auth(recruiter),
        )
    finally:
        use_case._judge, use_case._budget = original_judge, original_budget

    assert response.status_code == 429
    assert response.json()["code"] == "budget_exceeded"
    assert spy.seen == [], "nothing may be spent once the budget says no"
    shortlist = await client.get(
        f"/requisitions/{requisition_id}/shortlist", headers=auth(recruiter)
    )
    assert shortlist.json()[0]["assessment"] is None


class _Down:
    """A Redis that isn't there."""

    async def hit(self, *args: object, **kwargs: object) -> int:
        raise ConnectionError("redis unavailable")

    async def charge(self, *args: object, **kwargs: object) -> int:
        raise ConnectionError("redis unavailable")

    async def claim(self, *args: object, **kwargs: object) -> object:
        raise ConnectionError("redis unavailable")


async def test_when_redis_is_down_requests_still_work_but_spending_pauses(
    container: Container,
) -> None:
    down = _Down()
    limited = replace(
        container,
        settings=container.settings.model_copy(update={"rate_limits_enabled": True}),
        rate_limiter=down,
        idempotency=down,
    )
    app = create_app(limited.settings, container=limited)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        app.state.container = limited
        tokens = await workspace(client)  # rate limiter down: fails open
        created = await client.post(
            "/requisitions",
            json={"title": "Payments Engineer"},
            headers={**auth(tokens["recruiter"]), "Idempotency-Key": "k1"},  # store down: proceeds
        )
        assert created.status_code == 201

    # The budget fails closed: no metering, no paid calls.
    budget = MeteredBudget(down, calls_per_day=100)  # type: ignore[arg-type]
    from insightlm.shared.domain.errors import ServiceUnavailableError
    from insightlm.shared.domain.ids import new_tenant_id

    with pytest.raises(ServiceUnavailableError):
        await budget.charge(new_tenant_id(), 1)
