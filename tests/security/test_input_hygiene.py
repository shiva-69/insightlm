"""Found by schemathesis: text Postgres can't store must be a 422, never a 500."""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from insightlm.bootstrap import Container
from tests.conftest import requires_docker
from tests.integration.test_recruiting_flow import auth, workspace

pytestmark = [pytest.mark.integration, requires_docker]


# Sent as escaped JSON, as a client (or the fuzzer) would: a lone surrogate can't be
# encoded as UTF-8, so it only ever arrives as a \uXXXX escape.
@pytest.mark.parametrize("escaped", ["a\\u0000b", "lone \\ud83d surrogate"])
async def test_unstorable_text_is_refused_with_the_standard_envelope(
    client: AsyncClient, container: Container, escaped: str
) -> None:
    tokens = await workspace(client)
    response = await client.post(
        "/requisitions",
        content='{"title": "' + escaped + '"}',
        headers={**auth(tokens["recruiter"]), "content-type": "application/json"},
    )
    assert response.status_code == 422, response.text
    assert response.json()["code"] == "validation_error"


async def test_framework_validation_errors_use_the_same_envelope(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    response = await client.post("/requisitions", json={}, headers=auth(tokens["recruiter"]))
    body = response.json()
    assert response.status_code == 422
    assert body["code"] == "validation_error"
    assert body["message"].startswith("body.title")
    assert body["correlation_id"]


async def test_absurd_pagination_is_a_422_not_a_500(
    client: AsyncClient, container: Container
) -> None:
    tokens = await workspace(client)
    response = await client.get(
        "/documents?offset=11662714454027322333725619912704", headers=auth(tokens["owner"])
    )
    assert response.status_code == 422
