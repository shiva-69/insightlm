"""Authorisation at the HTTP boundary.

Every protected endpoint is exercised. A gap here is the kind that ships.
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

from insightlm.bootstrap import Container
from tests.conftest import requires_docker

pytestmark = [pytest.mark.integration, requires_docker]

PASSWORD = "correct-horse-battery-staple"

PROTECTED_ENDPOINTS = [
    ("GET", "/members"),
    ("POST", "/members/invitations"),
    ("GET", "/audit/entries"),
    ("POST", "/audit/verify"),
]


async def register(client: AsyncClient, slug: str) -> str:
    response = await client.post(
        "/auth/register",
        json={
            "tenant_name": slug.title(),
            "tenant_slug": slug,
            "email": f"owner@{slug}.example.com",
            "display_name": "Owner",
            "password": PASSWORD,
        },
    )
    assert response.status_code == 201, response.text

    login = await client.post(
        "/auth/login",
        json={"email": f"owner@{slug}.example.com", "password": PASSWORD},
    )
    assert login.status_code == 200, login.text
    token: str = login.json()["access_token"]
    return token


@pytest.mark.parametrize(("method", "path"), PROTECTED_ENDPOINTS)
async def test_every_protected_endpoint_rejects_an_anonymous_caller(
    client: AsyncClient, container: Container, method: str, path: str
) -> None:
    response = await client.request(method, path, json={})
    assert response.status_code == 401, f"{method} {path} allowed an anonymous caller"
    assert response.json()["code"] == "unauthenticated"


@pytest.mark.parametrize(("method", "path"), PROTECTED_ENDPOINTS)
async def test_every_protected_endpoint_rejects_a_forged_token(
    client: AsyncClient, container: Container, method: str, path: str
) -> None:
    forged = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiJ4In0.not-a-real-signature"
    response = await client.request(
        method, path, json={}, headers={"Authorization": f"Bearer {forged}"}
    )
    assert response.status_code == 401, f"{method} {path} accepted a forged token"


async def test_a_member_of_one_tenant_sees_only_that_tenant(
    client: AsyncClient, container: Container
) -> None:
    acme_token = await register(client, "acme")
    globex_token = await register(client, "globex")

    acme = await client.get("/members", headers={"Authorization": f"Bearer {acme_token}"})
    globex = await client.get("/members", headers={"Authorization": f"Bearer {globex_token}"})

    acme_emails = {m["user"]["email"] for m in acme.json()}
    globex_emails = {m["user"]["email"] for m in globex.json()}

    assert acme_emails == {"owner@acme.example.com"}
    assert globex_emails == {"owner@globex.example.com"}
    assert not (acme_emails & globex_emails)


async def test_another_tenants_membership_is_not_found_rather_than_forbidden(
    client: AsyncClient, container: Container
) -> None:
    """404, not 403.

    A 403 confirms the resource exists, which is itself information about another
    tenant. Absence is the only safe answer.
    """
    acme_token = await register(client, "acme")
    globex_token = await register(client, "globex")

    globex_members = await client.get(
        "/members", headers={"Authorization": f"Bearer {globex_token}"}
    )
    victim_id = globex_members.json()[0]["membership"]["id"]

    response = await client.patch(
        f"/members/{victim_id}/role",
        json={"role": "interviewer"},
        headers={"Authorization": f"Bearer {acme_token}"},
    )
    assert response.status_code == 404
    assert response.json()["code"] == "not_found"

    deletion = await client.delete(
        f"/members/{victim_id}", headers={"Authorization": f"Bearer {acme_token}"}
    )
    assert deletion.status_code == 404


async def test_an_interviewer_is_forbidden_from_inviting(
    client: AsyncClient, container: Container
) -> None:
    owner_token = await register(client, "acme")

    invite = await client.post(
        "/members/invitations",
        json={"email": "ivr@acme.example.com", "role": "interviewer"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    assert invite.status_code == 201, invite.text

    await client.post(
        "/auth/invitations/accept",
        json={
            "token": invite.json()["token"],
            "display_name": "Interviewer",
            "password": PASSWORD,
        },
    )
    login = await client.post(
        "/auth/login",
        json={"email": "ivr@acme.example.com", "password": PASSWORD},
    )
    interviewer_token = login.json()["access_token"]

    response = await client.post(
        "/members/invitations",
        json={"email": "someone@acme.example.com", "role": "recruiter"},
        headers={"Authorization": f"Bearer {interviewer_token}"},
    )
    assert response.status_code == 403
    assert response.json()["code"] == "permission_denied"

    audit = await client.get(
        "/audit/entries", headers={"Authorization": f"Bearer {interviewer_token}"}
    )
    assert audit.status_code == 403


async def test_responses_never_leak_password_hashes_or_token_hashes(
    client: AsyncClient, container: Container
) -> None:
    owner_token = await register(client, "acme")

    members = await client.get("/members", headers={"Authorization": f"Bearer {owner_token}"})
    assert "password_hash" not in members.text

    invite = await client.post(
        "/members/invitations",
        json={"email": "new@acme.example.com", "role": "recruiter"},
        headers={"Authorization": f"Bearer {owner_token}"},
    )
    body = invite.json()
    assert "token" in body
    assert "token_hash" not in invite.text


async def test_the_correlation_id_is_echoed_back(client: AsyncClient, container: Container) -> None:
    response = await client.get("/health", headers={"X-Correlation-ID": "trace-me-123"})
    assert response.headers["X-Correlation-ID"] == "trace-me-123"
