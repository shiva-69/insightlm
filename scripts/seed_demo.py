"""Seed a demo workspace through the public API.

    uv run python scripts/seed_demo.py                 # 20 cohort CVs, offline-friendly
    uv run python scripts/seed_demo.py --cohort 50 --out eval/.cache/demo.json

Everything goes through HTTP, exactly as a user's browser would, so the seed exercises
the same validation, permissions and audit trail as real use. Idempotent at the
workspace level: if the demo owner already exists it signs in and adds to it.

Spend: uploading embeds each CV, and assessing makes one judge call per criterion. With
a model key configured that's roughly (CVs x 3) calls; run the stack keyless
(GEMINI_API_KEY= GENERATION_PROVIDER=offline) for a free seed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from insightlm.evaluation.adapters.cohort import build_cohort

PASSWORD = "demo-password-change-me"  # noqa: S105 -- a documented demo credential
SLUG = "acme-demo"
PEOPLE = {
    "owner": ("priya@acme-demo.example", "Priya Sharma"),
    "recruiter": ("ravi@acme-demo.example", "Ravi Menon"),
    "hiring_manager": ("lena@acme-demo.example", "Lena Fischer"),
    "interviewer": ("ada@acme-demo.example", "Ada Mensah"),
}
RUBRIC = [
    {
        "label": "Python backend services",
        "kind": "must_have",
        "weight": 1.0,
        "evidence_hint": "services built and run in Python",
        "disqualifying": True,
    },
    {
        "label": "Payment processing",
        "kind": "strong_preference",
        "weight": 0.7,
        "evidence_hint": "settlement, reconciliation, card payments",
    },
    {"label": "Kubernetes in production", "kind": "nice_to_have", "weight": 0.3},
]


def log(message: str) -> None:
    sys.stderr.write(message + "\n")


class Api:
    def __init__(self, base: str) -> None:
        self._http = httpx.Client(base_url=base, timeout=120)

    def call(self, method: str, path: str, token: str | None = None, **kwargs: Any) -> Any:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        response = self._http.request(method, path, headers=headers, **kwargs)
        if response.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> {response.status_code}: {response.text[:300]}")
        return response.json() if response.content else None

    def login(self, email: str) -> str:
        token: str = self.call("POST", "/auth/login", json={"email": email, "password": PASSWORD})[
            "access_token"
        ]
        return token


def people(api: Api) -> dict[str, str]:
    owner_email, owner_name = PEOPLE["owner"]
    try:
        tokens = {"owner": api.login(owner_email)}
        log("demo workspace exists; signing in")
    except RuntimeError:
        api.call(
            "POST",
            "/auth/register",
            json={
                "tenant_name": "Acme Talent (demo)",
                "tenant_slug": SLUG,
                "email": owner_email,
                "display_name": owner_name,
                "password": PASSWORD,
            },
        )
        tokens = {"owner": api.login(owner_email)}
        log("created demo workspace")
    for role, (email, name) in PEOPLE.items():
        if role == "owner":
            continue
        try:
            tokens[role] = api.login(email)
        except RuntimeError:
            invite = api.call(
                "POST", "/members/invitations", tokens["owner"], json={"email": email, "role": role}
            )
            api.call(
                "POST",
                "/auth/invitations/accept",
                json={"token": invite["token"], "display_name": name, "password": PASSWORD},
            )
            tokens[role] = api.login(email)
    return tokens


def upload(api: Api, token: str, filename: str, payload: bytes) -> str:
    result = api.call(
        "POST", "/documents", token, files={"file": (filename, payload, "application/pdf")}
    )
    document_id: str = result["document"]["id"]
    for _ in range(120):
        status = api.call("GET", f"/documents/{document_id}/status", token)["status"]
        if status in ("ready", "failed"):
            break
        time.sleep(0.5)
    return document_id


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api", default="http://localhost:8000")
    parser.add_argument("--cohort", type=int, default=20)
    parser.add_argument("--out", type=Path, help="Write ids (for the load test) as JSON.")
    parser.add_argument(
        "--no-assess", action="store_true", help="Skip assessments (no judge calls)."
    )
    args = parser.parse_args()

    api = Api(args.api)
    tokens = people(api)
    recruiter, manager = tokens["recruiter"], tokens["hiring_manager"]

    requisition = api.call(
        "POST",
        "/requisitions",
        recruiter,
        json={
            "title": "Senior Payments Engineer",
            "description": "Owns settlement and reconciliation for card payments.",
        },
    )
    rubric = api.call(
        "POST", f"/requisitions/{requisition['id']}/rubrics", recruiter, json={"criteria": RUBRIC}
    )
    api.call("POST", f"/rubrics/{rubric['id']}/approve", manager)
    log(f"requisition {requisition['id']} with an approved rubric")

    samples = sorted(Path("samples").glob("0*.pdf")) if Path("samples").exists() else []
    applicants: list[tuple[str, str, bytes]] = [
        (
            p.stem.split("-", 1)[1].rsplit("-", 1)[0].replace("-", " ").title(),
            p.name,
            p.read_bytes(),
        )
        for p in samples
    ]
    applicants += [(a.name, a.filename, a.pdf) for a in build_cohort(args.cohort)]

    application_ids: list[str] = []
    for name, filename, payload in applicants:
        document_id = upload(api, recruiter, filename, payload)
        added = api.call(
            "POST",
            f"/requisitions/{requisition['id']}/candidates",
            recruiter,
            json={"name": name, "document_ids": [document_id]},
        )
        application_ids.append(added["application"]["id"])
        if not args.no_assess:
            api.call("POST", f"/applications/{added['application']['id']}/assessments", recruiter)
    log(f"{len(application_ids)} candidates added{'' if args.no_assess else ' and assessed'}")

    # Some pipeline movement, so every page has something on it.
    shortlist = api.call("GET", f"/requisitions/{requisition['id']}/shortlist", recruiter)
    members = {m["user"]["email"]: m["user"]["id"] for m in api.call("GET", "/members", recruiter)}
    for entry in shortlist[:3]:
        app_id = entry["application"]["id"]
        if entry["assessment"]:
            api.call("POST", f"/applications/{app_id}/stage", recruiter, json={"to": "screened"})
            api.call("POST", f"/applications/{app_id}/stage", recruiter, json={"to": "interview"})
            api.call(
                "POST",
                f"/applications/{app_id}/panel",
                recruiter,
                json={"user_id": members[PEOPLE["interviewer"][0]]},
            )
    for entry in shortlist[-2:]:
        api.call(
            "POST",
            f"/applications/{entry['application']['id']}/stage",
            recruiter,
            json={"to": "rejected", "reason": "skills_gap"},
        )

    summary = {
        "workspace": SLUG,
        "password": PASSWORD,
        "emails": {role: email for role, (email, _) in PEOPLE.items()},
        "requisition_id": requisition["id"],
        "application_ids": application_ids,
    }
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(summary, indent=2))
    sys.stdout.write(json.dumps(summary, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
