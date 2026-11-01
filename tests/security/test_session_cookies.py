"""Browser sessions: the token in an httpOnly cookie, and writes that prove same-origin.

The client here talks HTTPS, as a browser on the deployed site would, so Secure cookies
are stored and sent exactly as they would be in production.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest
from httpx import ASGITransport, AsyncClient

from insightlm.api.app import create_app
from insightlm.api.session import CSRF_COOKIE, SESSION_COOKIE
from insightlm.bootstrap import Container
from tests.conftest import requires_docker
from tests.integration.test_recruiting_flow import PASSWORD

pytestmark = [pytest.mark.integration, requires_docker]


@pytest.fixture
async def browser(container: Container) -> AsyncIterator[AsyncClient]:
    app = create_app(container.settings, container=container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="https://test") as http:
        app.state.container = container
        yield http


async def register_and_login(browser: AsyncClient, slug: str = "cookie") -> str:
    registered = await browser.post(
        "/auth/register",
        json={
            "tenant_name": slug.title(),
            "tenant_slug": slug,
            "email": f"owner@{slug}.example.com",
            "display_name": "Owner",
            "password": PASSWORD,
        },
    )
    assert registered.status_code == 201, registered.text
    login = await browser.post(
        "/auth/login", json={"email": f"owner@{slug}.example.com", "password": PASSWORD}
    )
    assert login.status_code == 200, login.text
    token: str = login.json()["access_token"]
    return token


async def test_login_sets_an_httponly_session_and_a_readable_csrf_cookie(
    browser: AsyncClient,
) -> None:
    await register_and_login(browser)
    headers = browser.cookies.jar
    by_name = {c.name: c for c in headers}
    session = by_name[SESSION_COOKIE]
    csrf = by_name[CSRF_COOKIE]
    assert session.secure and csrf.secure
    assert session.has_nonstandard_attr("HttpOnly") or "httponly" in str(session._rest).lower()
    assert not (csrf.has_nonstandard_attr("HttpOnly") or "httponly" in str(csrf._rest).lower())
    assert "lax" in str(session._rest).lower()


async def test_the_cookie_alone_authenticates_reads(browser: AsyncClient) -> None:
    await register_and_login(browser)
    me = await browser.get("/auth/session")
    assert me.status_code == 200, me.text
    assert me.json()["role"] == "owner"
    assert "member.invite" in me.json()["permissions"]


async def test_a_cookie_write_without_the_csrf_header_is_refused(browser: AsyncClient) -> None:
    await register_and_login(browser)
    body = {"title": "Engineer", "description": "Builds things."}

    forged = await browser.post("/requisitions", json=body)
    assert forged.status_code == 403, forged.text
    assert forged.json()["detail"]["reason"] == "csrf"

    wrong = await browser.post("/requisitions", json=body, headers={"X-CSRF-Token": "guessed"})
    assert wrong.status_code == 403, wrong.text

    proven = await browser.post(
        "/requisitions", json=body, headers={"X-CSRF-Token": browser.cookies[CSRF_COOKIE]}
    )
    assert proven.status_code == 201, proven.text


async def test_bearer_clients_need_no_csrf_header(browser: AsyncClient) -> None:
    token = await register_and_login(browser)
    browser.cookies.clear()
    created = await browser.post(
        "/requisitions",
        json={"title": "Engineer", "description": "Builds things."},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert created.status_code == 201, created.text


async def test_logout_ends_the_browser_session(browser: AsyncClient) -> None:
    await register_and_login(browser)
    out = await browser.post("/auth/logout")
    assert out.status_code == 204, out.text
    assert SESSION_COOKIE not in browser.cookies
    after = await browser.get("/auth/session")
    assert after.status_code == 401, after.text
