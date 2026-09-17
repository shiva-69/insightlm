# InsightLM — Evidence-Grounded Document Intelligence, applied to Hiring

> **Thesis in one sentence:** Recruiting is a document-intelligence problem, and the reason AI screening tools are distrusted and increasingly regulated is not that they are inaccurate — it is that they cannot show their work. InsightLM is a retrieval platform where **no AI claim exists without a citation to the exact span of the source document that supports it**, and hiring is its first vertical.

**Status:** Phase 0 complete — platform foundation built and tested.
**Type:** MCA Final Year Project (dissertation + working system).

---

## The two things this is

1. **A horizontal document-intelligence engine.** Upload documents, ask questions, get answers where every sentence links back to a highlighted span in the original PDF. Refuses to answer when the corpus does not support an answer.

2. **A recruiting vertical built on that engine.** A job description becomes a structured, human-approved rubric. Résumés are assessed criterion-by-criterion, each verdict carrying its evidence. Recruiters invite teammates, run a pipeline, and every AI-influenced decision is written to an append-only, hash-chained audit log stamped with the model and prompt version that produced it.

These are not two projects. The second is the first, specialised — same ingestion, same retrieval, same citation contract.

---

## Why this is not another "chat with your PDF"

| Typical student project | InsightLM |
| --- | --- |
| Naive chunk → embed → top-k → prompt | Layout-aware parse → structured extraction → hybrid (BM25 + dense) → RRF → cross-encoder rerank |
| "Trust the answer" | Citation-enforced generation; unsupported claims are rejected by a validator before reaching the user |
| No evaluation | Golden dataset, RAGAS + IR metrics, **ablation study** per pipeline stage, regression-gated in CI |
| AI outputs a score | LLM emits per-criterion *judgments with evidence*; deterministic code does the arithmetic |
| Ignores the law | Built to the shape of EU AI Act Annex III / NYC LL144 / GDPR Art. 22 obligations: human oversight, logging, transparency, contestability |
| No threat model | Résumé **prompt-injection** defence suite (hidden white text is a real, documented attack) with a red-team test corpus |

---

## Documentation map

| Doc | What is in it |
| --- | --- |
| [docs/01-charter-and-scope.md](docs/01-charter-and-scope.md) | Problem statement, research questions, personas, in/out of scope, success criteria |
| [docs/02-architecture.md](docs/02-architecture.md) | C4 views, service topology, data model, key decisions and their rationale |
| [docs/03-ai-engineering.md](docs/03-ai-engineering.md) | Ingestion, retrieval, generation, the citation contract, evaluation harness, safety |
| [docs/04-recruiting-and-governance.md](docs/04-recruiting-and-governance.md) | ATS domain model, rubric scoring, collaboration/RBAC, fairness and compliance layer |
| [docs/05-delivery-plan.md](docs/05-delivery-plan.md) | 16-week phased plan, milestones, demo script, risk register, compression options |
| [docs/06-engineering-standards.md](docs/06-engineering-standards.md) | Testing strategy, CI/CD, observability, security, definition of done |
| [docs/adr/README.md](docs/adr/README.md) | Architecture Decision Records — index, template, and the decisions to write |

**Read order for a reviewer:** 01 → 02 → 03 → 04. **Read order for building:** 05 → 02 → 06.

---

## Stack, in one line

**Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0 async · Alembic · ARQ · Postgres 17 + pgvector + RLS · Redis · MinIO · Next.js 15** — a single service, structured hexagonally (`domain / ports / application / adapters` per module), with `import-linter` and `mypy --strict` enforcing the boundaries in CI.

One language, because the dissertation's contribution — the evaluation harness and the ablation study — is Python-native, and a cross-language split would tax infrastructure that is not the contribution. The boundaries that matter are drawn *inside* the process and enforced by tooling. See [ADR-0001](docs/adr/README.md) and [docs/02 §1](docs/02-architecture.md).

---

## Running it

```bash
make up          # postgres + pgvector, redis, minio, otel, grafana, api, worker
open http://localhost:8000/docs
```

Migrations run as their own compose service before the app starts, and they create the
restricted database role the application connects as. Nothing else needs doing.

For local development against the compose infrastructure:

```bash
make install     # uv sync --all-groups
make migrate
make dev         # uvicorn with reload
make worker      # arq, in another shell
```

```bash
make check       # ruff + mypy --strict + import-linter
make test        # everything (needs Docker for testcontainers)
make test-fast   # unit + architecture only, no Docker
```

### Try the Phase 0 exit criteria

```bash
# create a workspace and its owner
curl -sX POST localhost:8000/auth/register -H 'content-type: application/json' -d '{
  "tenant_name":"Acme","tenant_slug":"acme","email":"owner@acme.com",
  "display_name":"Owner","password":"correct-horse-battery-staple"}'

# log in
TOKEN=$(curl -sX POST localhost:8000/auth/login -H 'content-type: application/json' \
  -d '{"email":"owner@acme.com","password":"correct-horse-battery-staple"}' | jq -r .access_token)

# invite a teammate -- the token comes back exactly once
curl -sX POST localhost:8000/members/invitations -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"email":"recruiter@acme.com","role":"recruiter"}'

# read the audit chain, then verify it end to end
curl -s localhost:8000/audit/entries -H "authorization: Bearer $TOKEN" | jq
curl -sX POST localhost:8000/audit/verify -H "authorization: Bearer $TOKEN" | jq
```

---

## What Phase 0 built

| Capability | Where |
| --- | --- |
| Hexagonal modules, boundaries enforced in CI | [`.importlinter`](.importlinter), [`src/insightlm/`](src/insightlm/) |
| Unit of work — one commit site, no implicit commit | [`shared/adapters/db/uow.py`](src/insightlm/shared/adapters/db/uow.py) |
| Tenant isolation by Postgres RLS, failing closed | [migration 0001](migrations/versions/0001_platform_foundation.py), [ADR-0016](docs/adr/0016-row-level-security-for-tenancy.md) |
| Hash-chained audit log, three enforcement layers | [`governance/domain/audit.py`](src/insightlm/governance/domain/audit.py) |
| Roles, permission matrix, deny-by-default policy | [`shared/domain/roles.py`](src/insightlm/shared/domain/roles.py) |
| Invitations — single-use, expiring, tenant-bearing tokens | [`identity/domain/invitation_token.py`](src/insightlm/identity/domain/invitation_token.py) |
| Transactional outbox + relay | [`shared/adapters/db/relay.py`](src/insightlm/shared/adapters/db/relay.py) |
| Prompt and model version registry | [`governance/adapters/models.py`](src/insightlm/governance/adapters/models.py) |
| PDF char-range → bounding box spike | [`spikes/pdf_geometry_spike.py`](spikes/pdf_geometry_spike.py) |

Six ADRs written: [0001](docs/adr/0001-single-python-service-hexagonal.md),
[0002](docs/adr/0002-postgres-pgvector-single-store.md),
[0003](docs/adr/0003-in-app-jwt-auth.md),
[0005](docs/adr/0005-unit-of-work-contract.md),
[0014](docs/adr/0014-arq-and-transactional-outbox.md),
[0016](docs/adr/0016-row-level-security-for-tenancy.md).

---

## Working assumptions

These shaped the plan. Correct any that are wrong and the plan should be revised.

- **Timeline:** ~16 working weeks, starting 22 Sep 2026, code freeze mid-Jan 2027, buffer for viva after. Compression and extension paths are in [docs/05-delivery-plan.md](docs/05-delivery-plan.md).
- **Team:** solo, part-time alongside a job (~15–20 hrs/week).
- **Budget:** a modest hosted-LLM budget (tens of dollars, not hundreds), with a local-model fallback path so a dead API key never blocks a demo.
- **Deployment:** `docker compose` is the supported path and what gets demoed; Kubernetes manifests exist as a "this is how it would ship" artifact, not as a hosting requirement.
- **Grading audience:** an examiner who is technically literate but not an LLM specialist. The system must be explicable in ten minutes and impressive in three.
