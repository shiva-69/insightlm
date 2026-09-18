# InsightLM — Evidence-Grounded Document Intelligence, applied to Hiring

> **Thesis in one sentence:** Recruiting is a document-intelligence problem, and the reason AI screening tools are distrusted and increasingly regulated is not that they are inaccurate — it is that they cannot show their work. InsightLM is a retrieval platform where **no AI claim exists without a citation to the exact span of the source document that supports it**, and hiring is its first vertical.

**Status:** Phase 1 complete — document engine working end to end. Upload a PDF, ask a question, click a citation, see the exact span highlighted.
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
make up          # postgres + pgvector, redis, minio, otel, grafana, api, worker, web
open http://localhost:3000          # the app
open http://localhost:8000/docs     # the API
```

> **MinIO comes from `quay.io`, not Docker Hub.** MinIO no longer publishes
> `minio/minio` there, and the pull fails with *"repository does not exist or may
> require 'docker login'"* — which reads like an auth problem and is not one. The
> image is pinned by digest in [compose.yaml](compose.yaml); the comment there says
> how to bump it.

Migrations run as their own compose service before the app starts, and they create the
restricted database role the application connects as. Nothing else needs doing.

For local development against the compose infrastructure:

Toolchain: **Python 3.12** (managed by `uv`) and **Node LTS** via `nvm`
(`nvm install --lts`). Nothing else is required — `uv` fetches its own Python.

```bash
cd web && npm install && npm run dev    # http://localhost:3000
```

Next proxies `/api/*` to the API, so the browser only ever talks to its own origin —
no CORS configuration, and the bearer token never goes to a third-party origin.

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

### The demo

Open **http://localhost:8000** — register, upload a PDF, ask a question, click a
citation. The span highlights in the rendered page.

### Try it from the shell

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

# upload a document, then watch it through the pipeline
DOC=$(curl -sX POST localhost:8000/documents -H "authorization: Bearer $TOKEN" \
  -F "file=@cv.pdf" | jq -r .document.id)
curl -s localhost:8000/documents/$DOC/status -H "authorization: Bearer $TOKEN" | jq

# ask -- every claim carries a citation that resolves to an exact span
curl -sX POST localhost:8000/ask -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"question":"What payment systems experience is there?"}' | jq '.claims, .citations[0]'
```

---

## What Phase 1 built

The vertical slice: **upload → parse → chunk → embed → index → retrieve → generate →
validate → highlight.**

| Capability | Where |
| --- | --- |
| Layout-aware parsing with per-character geometry, index-aligned by contract | [`pymupdf_parser.py`](src/insightlm/documents/adapters/pymupdf_parser.py) |
| Structure-aware chunking; sections are hard boundaries | [`chunker.py`](src/insightlm/retrieval/adapters/chunker.py), [ADR-0004](docs/adr/0004-structure-aware-chunking.md) |
| **The citation contract** — claims validated before display | [`answer.py`](src/insightlm/retrieval/domain/answer.py), [ADR-0006](docs/adr/0006-the-citation-contract.md) |
| `EvidencePort` — the one seam Phase 4 will depend on | [`ports/evidence.py`](src/insightlm/retrieval/ports/evidence.py) |
| Resumable pipeline; a failed embed does not re-parse | [`worker/ingestion.py`](src/insightlm/worker/ingestion.py) |
| Dead-letter table — a document that fails is visible, never absent | [migration 0002](migrations/versions/0002_document_engine.py) |
| pgvector HNSW + trigger-maintained tsvector, both indexed | [migration 0002](migrations/versions/0002_document_engine.py) |
| Grounded generation via `claude-opus-5`, structured output | [`anthropic_generator.py`](src/insightlm/retrieval/adapters/anthropic_generator.py) |
| Offline deterministic fallbacks — runs with no API key | [`embedding.py`](src/insightlm/retrieval/adapters/embedding.py), [`extractive_generator.py`](src/insightlm/retrieval/adapters/extractive_generator.py) |
| Evidence viewer — click a citation, highlight the span | [`web/`](web/), plus a no-toolchain fallback at [`viewer.html`](src/insightlm/api/static/viewer.html) |

**It runs without an API key.** `HashingEmbedder` and `ExtractiveGenerator` are
deterministic and offline, so CI, a clean checkout and a live demo all work with no
account. Set `ANTHROPIC_API_KEY` (and optionally `VOYAGE_API_KEY`) to switch to the
real models; the container picks them up automatically. The offline path's quality
ceiling is real and documented — Phase 2's eval harness measures the gap rather than
assuming it.

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

## The interface

Next.js 15 App Router, TypeScript, Tailwind v4, TanStack Query, PDF.js.

One design rule carries the whole thing: **the only saturated colour in the interface
is evidence.** Chrome, text, borders and states all live on a single warm neutral ink
scale; amber appears exclusively where a highlight marks a cited passage. Colour in
this product therefore always means *this is the evidence* — which is a constraint
rather than a palette, and it is what keeps the UI from drifting into the
gradient-and-glass look that afflicts tools in this category.

Three type families, each with a job: **Inter** for chrome you scan, **Newsreader**
for prose you read, **JetBrains Mono** for data you parse — model versions, hashes,
character offsets. Hairline rules instead of drop shadows; 4–8px radii; 120–180ms
transitions with no spring.

The citation link is bidirectional: hovering a chip in the answer lights its highlight
in the document, and clicking a highlight activates the claim that cited it.

`npm run walkthrough` drives a browser through the whole product and screenshots each
step. It caught two defects nothing else would have: fonts silently falling back to
the system stack, and claims arriving as mid-sentence fragments.

---

## Working assumptions

These shaped the plan. Correct any that are wrong and the plan should be revised.

- **Timeline:** ~16 working weeks, starting 22 Sep 2026, code freeze mid-Jan 2027, buffer for viva after. Compression and extension paths are in [docs/05-delivery-plan.md](docs/05-delivery-plan.md).
- **Team:** solo, part-time alongside a job (~15–20 hrs/week).
- **Budget:** a modest hosted-LLM budget (tens of dollars, not hundreds), with a local-model fallback path so a dead API key never blocks a demo.
- **Deployment:** `docker compose` is the supported path and what gets demoed; Kubernetes manifests exist as a "this is how it would ship" artifact, not as a hosting requirement.
- **Grading audience:** an examiner who is technically literate but not an LLM specialist. The system must be explicable in ten minutes and impressive in three.
