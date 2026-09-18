"""Register a throwaway workspace on the local API and print its access token."""

from __future__ import annotations

import secrets

import httpx

API = "http://localhost:8000"
PASSWORD = secrets.token_urlsafe(24)  # throwaway: the workspace exists only for the run

suffix = secrets.token_hex(4)
email = f"fuzz-{suffix}@example.com"
httpx.post(
    f"{API}/auth/register",
    json={
        "tenant_name": f"Fuzz {suffix}",
        "tenant_slug": f"fuzz-{suffix}",
        "email": email,
        "display_name": "Fuzz",
        "password": PASSWORD,
    },
).raise_for_status()
login = httpx.post(f"{API}/auth/login", json={"email": email, "password": PASSWORD})
login.raise_for_status()
print(login.json()["access_token"])  # noqa: T201 -- the token is this script's output
