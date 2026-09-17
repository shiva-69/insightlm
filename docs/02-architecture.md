# 02 — Architecture

## 1. Shape of the system

**A single Python service, structured hexagonally, with a Next.js frontend.**

One codebase, two entrypoints — an API process and a worker process — sharing the same domain code. The restraint is deliberate, and it is the opposite of the usual student instinct to demonstrate sophistication through topology.

The reasoning, which belongs in ADR-0001:

- The dissertation's contribution is the **evaluation harness and the ablation study**. That work is Python-native — layout models, OCR, cross-encoder rerankers, the metrics ecosystem. Putting the transactional core in another language would mean paying a cross-language integration tax (contract tests, DTOs defined twice, cross-boundary debugging) on infrastructure that is *not* the contribution.
- The hiring domain is **transactionally coupled** — a stage transition writes a candidate row, an audit entry, an outbox event and an SLA timer, and those must commit or fail together. A service boundary here buys distributed-transaction problems and nothing else.
- So there is no service boundary. The boundaries that matter are drawn **inside** the process, and enforced by tooling rather than convention.

**What stops this being a big ball of mud** — and this is the part that has to be real, because "Python monolith" defaults to spaghetti:

1. Every module is split `domain / ports / application / adapters`. Domain code imports nothing but the standard library.
2. Modules talk to each other **only through `Protocol` ports**. `recruiting` cannot import `retrieval.adapters` — it cannot even import `retrieval.application`.
3. **`import-linter` runs in CI and fails the build** on any violation. Not a guideline; a gate.

That third point is what makes the claim in [01](01-charter-and-scope.md) — *recruiting is a vertical on a horizontal document engine* — a provable property of the codebase rather than a sentence in a report.

```
┌──────────────────────────────────────────────────────────────────────┐
│  Next.js 15 (App Router) · TypeScript · Tailwind + shadcn/ui         │
│  TanStack Query · PDF.js evidence viewer · SSE streaming             │
└───────────────────────────────┬──────────────────────────────────────┘
                                │ REST + SSE, JWT bearer
┌───────────────────────────────▼──────────────────────────────────────┐
│  InsightLM — Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2.0    │
│                                                                      │
│  ┌── api process (uvicorn) ──────┐  ┌── worker process (ARQ) ─────┐  │
│  │  HTTP, SSE, auth, use cases   │  │  ingest, embed, assess, eval │  │
│  └───────────────┬───────────────┘  └──────────────┬──────────────┘  │
│                  └──────── same domain code ───────┘                 │
│                                                                      │
│   identity  │ documents │ retrieval │ recruiting │ governance │ eval  │
│   ─────────────────────────────────────────────────────────────────  │
│   each module:  domain/ → ports/ → application/ → adapters/          │
│   cross-module imports allowed ONLY into another module's ports/     │
│                                                                      │
│   shared kernel: unit-of-work · outbox · idempotency · correlation   │
│                  · policy engine · result types                      │
└──┬──────────────────┬───────────────────┬────────────────────────────┘
   │ asyncpg          │ redis             │ S3 API
┌──▼───────────────┐ ┌▼────────────────┐ ┌▼──────────────────┐
│ PostgreSQL 17    │ │ Redis           │ │ MinIO / S3        │
│ + pgvector (HNSW)│ │ ARQ queue + DLQ │ │ raw documents,    │
│ + tsvector       │ │ cache, locks,   │ │ rendered pages    │
│ + ROW LEVEL SEC. │ │ rate limit,     │ └───────────────────┘
│ relational AND   │ │ semantic cache  │ ┌───────────────────┐
│ vectors, ONE db  │ └─────────────────┘ │ OTel → Prometheus │
└──────────────────┘                     │ Grafana·Loki·Tempo│
   │                                     └───────────────────┘
   │ httpx (circuit-broken)
┌──▼──────────────────────────────┐
│ LLM + embedding providers       │
│ primary: hosted API             │
│ fallback: local (Ollama)        │
└─────────────────────────────────┘
```

Note what is **gone** versus a conventional design: no second language, no RabbitMQ, no gRPC layer, no contract tests. Five infrastructure containers become three. Durability does not suffer, because the **transactional outbox lives in Postgres** — the queue is a work distributor, not a source of truth.

## 2. The hexagonal structure

Inside every module:

```
recruiting/
  domain/        entities, value objects, invariants.  Imports: stdlib only.
  ports/         Protocol classes — what this module NEEDS and what it OFFERS.
  application/   use cases, orchestration, unit-of-work usage.
                 Imports: domain + ports. NEVER adapters.
  adapters/      sqlalchemy repos, http clients, llm calls, s3.
                 Imports: domain + ports + the outside world.
```

Dependencies point **inward**. Adapters depend on ports; the domain depends on nothing. Wiring happens once, in a composition root (`bootstrap.py`), surfaced to FastAPI through `Depends`. No DI framework — a container is not needed when there is one wiring site you can read top to bottom.

### The port that carries the thesis

`retrieval` offers exactly one thing to the rest of the system:

```python
# retrieval/ports/evidence.py
class EvidencePort(Protocol):
    async def find(self, query: EvidenceQuery) -> EvidenceBundle: ...
```

`EvidenceQuery` and `EvidenceBundle` are domain types built from document IDs and spans. Nothing about embeddings, chunking, rerankers or vector SQL crosses this line. `recruiting` depends on `EvidencePort` and is structurally incapable of knowing how retrieval works.

**In the viva, this is the single artifact to point at.** Open `recruiting/`, show that the only import from `retrieval` is a Protocol, then show the import-linter contract that makes it impossible to add another.

### The unit of work — what replaces `@Transactional`

This is the one thing Spring gives free that Python makes you build, so build it deliberately:

```python
# shared/ports/uow.py
class UnitOfWork(Protocol):
    documents:   DocumentRepository
    applications: ApplicationRepository
    audit:       AuditLog
    outbox:      Outbox

    async def __aenter__(self) -> "UnitOfWork": ...
    async def __aexit__(self, *exc) -> None: ...   # rolls back unless commit() ran
    async def commit(self) -> None: ...
```

Rules, enforced by review and by the linter contract:

- **One session per request or task**, opened at the use-case boundary, never inside a repository.
- **`session.commit()` appears in exactly one file** — the SQLAlchemy UoW adapter. Anywhere else is a bug.
- Repositories receive a session; they never create one.
- Domain events accumulate on the UoW and are flushed to the outbox **inside the same transaction** as the domain write.

> Async SQLAlchemy's real footgun is a session leaking across a task boundary — `asyncio.gather` over coroutines that share one session produces corruption that surfaces as impossible-looking bugs. Rule: **fan-out work gets its own session each, or does no I/O at all.** Write this in the contributing guide and add a test that asserts session identity per task.

## 3. Modules and their boundaries

| Module | Owns | Offers (port) | Must not know about |
| --- | --- | --- | --- |
| `identity` | Tenants, users, memberships, invitations, roles, API keys | `ActorPort`, `PolicyPort` | Anything domain-specific |
| `documents` | Upload, storage refs, ingestion lifecycle, versions, chunks | `DocumentPort`, `IngestPort` | Recruiting concepts |
| `retrieval` | Query planning, hybrid search, fusion, rerank, citation assembly, validation | **`EvidencePort`** | Why the caller is asking |
| `recruiting` | Jobs, rubrics, candidates, applications, stages, scorecards, assessments | `PipelinePort` | How retrieval works internally |
| `governance` | Audit chain, prompt/model registry, retention, redaction, consent | `AuditPort`, `RedactionPort` | — (depended on by all) |
| `evaluation` | Golden datasets, eval runs, metrics, ablation configs, reports | — (CLI-driven) | Production request paths |

### The import-linter contracts

`.importlinter` in the repo root — this file *is* the architecture:

```ini
[importlinter]
root_package = insightlm

[importlinter:contract:layers]
name = Hexagonal layering holds inside every module
type = layers
layers = adapters | application | ports | domain
containers =
    insightlm.identity
    insightlm.documents
    insightlm.retrieval
    insightlm.recruiting
    insightlm.governance
    insightlm.evaluation

[importlinter:contract:modules]
name = Modules are independent except through ports
type = independence
modules =
    insightlm.identity
    insightlm.documents
    insightlm.retrieval
    insightlm.recruiting
    insightlm.evaluation
# governance is intentionally omitted — it is a shared dependency

[importlinter:contract:domain-purity]
name = Domain layers touch no infrastructure
type = forbidden
source_modules  = insightlm.*.domain
forbidden_modules = sqlalchemy, httpx, redis, fastapi, arq, boto3, openai
```

The `independence` contract is strict, so cross-module port usage is declared as explicit allowances. That friction is the point: adding a dependency between modules becomes a visible, reviewed change to an architecture file, not an `import` someone slips in at midnight.

**Commit a deliberate violation in Week 1 and screenshot CI rejecting it.** That screenshot is worth a paragraph in the dissertation.

## 4. Technology choices and their justifications

| Choice | Why | When it would be wrong |
| --- | --- | --- |
| **Python 3.12 + FastAPI + Pydantic v2** | The research contribution is Python-shaped; one ecosystem, one CI path, one debugger. Pydantic gives typed boundaries and free OpenAPI | If the domain were compute-bound rather than I/O-bound |
| **SQLAlchemy 2.0 async + Alembic** | The only mature Python ORM with a real unit-of-work story; 2.0's typed API works with `mypy --strict` | — |
| **PostgreSQL + pgvector (HNSW)** | One store for rows *and* vectors: chunk metadata and its embedding commit atomically. Erasure, tenant filtering and relational joins all become trivial — and tenant-filtered ANN is exactly where standalone vector DBs get awkward | Beyond ~10M vectors at high QPS → Qdrant; the port is shaped so this is one adapter |
| **Postgres Row-Level Security for tenancy** | Tenancy enforced by the *database*, not by remembering a `WHERE` clause. `SET LOCAL app.tenant_id` per transaction; a missing filter fails closed instead of leaking | — (this is strictly better than app-layer filtering) |
| **Postgres `tsvector` for lexical retrieval** | Avoids running OpenSearch for one feature. Exact-term recall (`Kubernetes`, `PCI-DSS`, `CKA`) is where dense retrieval genuinely fails, and in a hiring corpus that is the common case | If lexical quality measurably caps Recall@k |
| **ARQ on Redis** *(not Celery, not RabbitMQ)* | Async-native, small enough to read the source of, and reuses Redis you already run. Celery's threading model fights asyncio; RabbitMQ is a whole broker for one queue | If you needed fan-out across many worker fleets |
| **Transactional outbox in Postgres** | Domain write + event publish in one commit. Durability never depends on the broker, so ARQ's weaker delivery guarantees stop mattering | Never, at this scale |
| **`tenacity` + a hand-rolled circuit breaker** | Retry-with-jitter is one decorator; a breaker is ~40 lines and you can explain every one of them. `pybreaker` if you prefer a library | — |
| **Direct provider SDKs + a thin `LLMPort`** *(not LangChain)* | You need exact token/cost accounting for RQ3 and exact prompt control for versioning. Framework abstraction obscures both, and "we used LangChain" is a weak viva answer | If you needed many providers fast |
| **`uv` for dependency management** | Python's genuine weak spot; `uv` makes it fast and lockfile-deterministic | — |
| **Next.js 15 + TypeScript** | SSE streaming, PDF.js integration; shadcn/ui gives a credible UI without a designer | — |
| **Two Docker targets from one Dockerfile** | `api` stays slim (no torch); `worker` carries parsers, OCR and the reranker. Keeps API deploys fast and the attack surface small | If the reranker moves to a hosted API — then one slim image |

**On auth:** in-app JWT with `python-jose` / `pyjwt` + `argon2` password hashing, not Keycloak. Keycloak is a large operational dependency for one tenant model, and *auth is a thing an examiner will ask you to explain line by line.*

## 5. Data model (core entities)

Everything below is tenant-scoped, enforced by **Postgres RLS policies** — never by per-query `WHERE` clauses.

```
tenant ──< membership >── user
  │                         └──< invitation
  │
  ├──< document ──< document_version ──< chunk ──< chunk_embedding (vector)
  │        │                                 └──< chunk_span (page, bbox, char range)
  │        └──< ingestion_job ──< ingestion_step (typed, resumable)
  │
  ├──< job_requisition ──< rubric ──< rubric_criterion (weight, kind, threshold)
  │           │                         └──< criterion_approval (who, when)
  │           └──< pipeline_stage (ordered, SLA)
  │
  ├──< candidate ──< candidate_document → document
  │        │        └──< extracted_profile (typed: experiences, skills, education —
  │        │                                each field with its evidence span)
  │        └──< identity_link (dedup / merge history)
  │
  ├──< application (candidate × job_requisition)
  │        ├──< assessment ──< criterion_judgment (verdict, confidence, citations[])
  │        │         └── (model_version, prompt_version, retrieval_config_hash)
  │        ├──< stage_transition (from, to, actor, reason, at)
  │        └──< scorecard ──< scorecard_item (interviewer, criterion, rating, notes)
  │
  └──< audit_entry (append-only, prev_hash, entry_hash, actor, action, subject, payload)

conversation ──< message ──< message_citation → chunk_span
prompt_template ──< prompt_version (immutable, content-hashed)
eval_dataset ──< eval_item ──< eval_run_result → eval_run (config snapshot)
outbox_event · retention_policy · consent_record · redaction_profile
```

**Design notes worth defending in the viva:**

- **`document_version`, not `document`.** A candidate sends an updated résumé; you must not silently invalidate an assessment that cited the old one. Assessments pin a version.
- **`criterion_judgment` holds `citations[]`, not prose.** The judgment is *structured data*; the narrative is rendered from it. This is what makes scores recomputable.
- **`chunk_span` carries page + bounding box + char offsets.** Char offsets alone cannot drive a PDF highlight. Capture geometry at parse time or the feature becomes impossible to add later.
- **`audit_entry.prev_hash`** makes the log tamper-evident: any mutation breaks the chain, and a verifier proves it. Enforce append-only with a DB grant *and* an RLS policy, so even a compromised app cannot rewrite history.
- **`retrieval_config_hash`** on assessments answers "would this candidate have scored differently under the new pipeline?" — which is an experiment, and experiments are what dissertations are made of.

## 6. Request flows

**Ingestion (async, resumable, idempotent)**
```
upload → checksum → dedup check → store in S3 → create document_version
  → enqueue INGEST_REQUESTED via outbox (same transaction as the write)
  → outbox relay → ARQ
  → worker: parse (layout+OCR) → sanitise (injection scan, PII detect)
    → chunk (structure-aware) → embed (batched) → index (pgvector + tsvector)
    → extract typed profile (if résumé) → mark READY
  → each step recorded in ingestion_step; retry resumes from the last good step
  → exhausted retries → dead-letter table with a visible admin view
```
Idempotency key = `(tenant, content_checksum, pipeline_version)`. Re-uploading the same file is free; bumping the pipeline version reprocesses deliberately.

**Grounded query (streaming)**
```
question → guardrail (scope + injection) → query plan (rewrite / decompose / HyDE)
  → asyncio.gather: BM25 (tsvector) ∥ dense (pgvector HNSW)
  → RRF fusion → cross-encoder rerank (top 50 → top 8)
  → context assembly with stable chunk IDs
  → LLM, structured output: { claims: [{ text, citation_ids[] }] }
  → citation validator: every claim resolves to a real span, else reject/retry
  → stream via SSE; UI resolves citation → highlights span in PDF.js
```

**Assessment (the recruiting application of the above)**
```
async with uow:
    for each approved rubric_criterion, concurrently (bounded semaphore):
        EvidencePort.find(criterion, candidate_document_scope)
          → EvidenceBundle (ranked spans)
          → LLM judges ONLY this criterion against ONLY this evidence
          → { verdict ∈ {strong, adequate, weak, absent}, confidence, citations[] }
    # fan-out does pure I/O against the LLM and holds no DB session
    aggregate deterministically (weights from the approved rubric)
      → score + explanation rendered from judgments
      → stamped with model/prompt/config versions
      → audit entry written
    await uow.commit()          # judgments + assessment + audit, one transaction
```

Per-criterion isolation is the key move. The model never sees the whole résumé and is never asked "how good is this person" — it answers narrow, evidence-bounded questions. Narrower questions hallucinate less, are individually testable, parallelise cleanly, and the aggregate stays in your code where you can defend it.

Note the session discipline: the concurrent fan-out touches no database. Results come back in memory and are persisted in one transaction on the way out.

## 7. Cross-cutting concerns

- **Tenancy:** JWT carries `tenant_id`; a middleware sets `SET LOCAL app.tenant_id` at transaction start; RLS policies do the rest. One integration test attempts cross-tenant access on **every** endpoint and expects `404`, not `403` — do not leak existence.
- **Authorisation:** a single `PolicyPort.can(actor, action, resource)` component, deny-by-default. Not decorators scattered across routers — rubric approval and scorecard visibility need real rules.
- **Resilience:** `tenacity` retry-with-jitter plus a circuit breaker around every LLM and embedding call, with a bounded semaphore as the bulkhead. Open circuit → degrade to local model → degrade to "retrieval only, no synthesis" with an honest banner. **Demo this on purpose** by killing the provider; graceful degradation under live failure is the most senior thing you can show an examiner.
- **Cost control:** token accounting on every call, attributed to tenant and feature; per-tenant budget caps; semantic cache in Redis keyed on normalised-query embedding similarity.
- **Config:** `pydantic-settings`, typed, no magic strings; secrets from environment; one settings class a reviewer can read top to bottom.
- **Observability:** OpenTelemetry auto-instrumentation for FastAPI, SQLAlchemy and httpx, plus manual spans around pipeline stages carrying `tenant_id`, `correlation_id`, `model`, `prompt_version`, token counts and cost. One trace should explain an entire assessment.
- **Errors:** typed `Result` / domain exceptions at module boundaries, mapped to HTTP once in an exception handler. Never `None` as a sentinel across a port.

## 8. Documented reversibility

The hexagonal structure is what makes this cheap, and saying so is the point of doing it.

- **Swapping pgvector for Qdrant** touches one adapter behind `VectorStorePort`. The eval harness proves the swap did not regress retrieval.
- **Extracting a service** — if the worker ever needed to scale independently, `documents` and `retrieval` are already pure-port consumers; the adapter becomes an HTTP client and the application layer does not change. That is the *whole* argument for hexagonal: the decision is deferred at near-zero cost, rather than paid for up front.
- **Moving the reranker to a hosted API** drops torch, collapsing the worker image from ~3GB to ~250MB.

Record all three in ADRs with their estimated migration cost. Anyone can justify a choice; knowing its expiry date is the senior move.
