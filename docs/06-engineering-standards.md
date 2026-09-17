# 06 — Engineering Standards

These standards are what make the codebase *read* as senior work. Reviewers form their judgment in the first ten minutes of looking at a repo, and it is formed by structure, tests and consistency — not by feature count.

For a Python monolith this matters more than it would elsewhere. The stack imposes almost no structure of its own, so every boundary that exists has to be one you chose and one that tooling enforces.

## 1. Repository layout

```
insightlm/
├── src/insightlm/
│   ├── identity/  documents/  retrieval/  recruiting/  governance/  evaluation/
│   │      each module:
│   │        domain/        entities, value objects, invariants — stdlib only
│   │        ports/         Protocol classes (what it needs, what it offers)
│   │        application/   use cases, unit-of-work orchestration
│   │        adapters/      sqlalchemy, httpx, llm, s3 — the outside world
│   ├── shared/             uow, outbox, idempotency, correlation, policy, result types
│   ├── api/                FastAPI routers, schemas, exception handlers, deps
│   ├── worker/             ARQ task definitions (thin — they call use cases)
│   └── bootstrap.py        the composition root: the ONE place adapters meet ports
├── migrations/             Alembic
├── eval/                   golden datasets, runner, ablation configs, reports
├── tests/                  unit/  integration/  security/  e2e/
├── web/                    Next.js 15 App Router
├── infra/                  compose, k8s, grafana dashboards, k6
├── .importlinter           ← the architecture, as an enforced file
├── pyproject.toml          ruff, mypy, pytest config; uv-managed deps
└── docs/                   including adr/
```

Two rules carry the whole structure:

1. **Dependencies point inward.** `adapters → application → ports → domain`. Domain imports nothing but stdlib.
2. **Modules meet only at ports.** `recruiting` may import `retrieval.ports`; importing `retrieval.application` or `retrieval.adapters` fails the build.

Both are enforced by `import-linter` in CI, not by convention ([02 §3](02-architecture.md#the-import-linter-contracts) has the contracts). The difference between *having* a hexagonal architecture and *claiming* one is exactly this file.

## 2. Type discipline

Python's weakest point against the Java alternative, so over-correct:

- **`mypy --strict` on `src/`, no exceptions, no `# type: ignore` without a comment explaining why.** Wire it into CI before writing the second module, never after.
- **Pydantic models at every I/O boundary** — HTTP requests/responses, LLM structured outputs, task payloads. Parse at the edge, and the interior handles typed objects only.
- **Domain entities are `@dataclass(frozen=True, slots=True)`** where they are values, mutable only where they have identity and a lifecycle. Invariants live in `__post_init__` — an invalid `Rubric` must be unconstructable.
- **`Protocol` for every port**, never ABCs. Structural typing means adapters do not import the port to satisfy it, which keeps the dependency arrow honest.
- **Verdicts and results are `Enum` or tagged unions**, never bare strings. `verdict == "strong"` is a typo waiting to happen; `Verdict.STRONG` is not.
- **No `Any` in `src/`.** If something genuinely needs it, that is a design smell worth an ADR.

## 3. Testing strategy

| Level | What | Tools | Target |
| --- | --- | --- | --- |
| Unit | Domain rules — aggregation maths, date-interval merge, hash chain, policy decisions, RRF | pytest, **hypothesis** | Pure functions, no I/O, no fixtures |
| Integration | Repositories, migrations, **RLS policies**, outbox relay, queue round-trips | **testcontainers-python** (Postgres+pgvector, Redis, MinIO) | Real infra, no DB mocks |
| Architecture | Layering + module independence | `import-linter` | Build gate |
| Security | Cross-tenant access, role matrix, injection defences, erasure residue | pytest + httpx AsyncClient | **Every endpoint** |
| API contract | Schema conformance, fuzzing | **schemathesis** against the generated OpenAPI | Nightly |
| Pipeline | Retrieval + generation quality | The eval harness in CI | Regression-gated |
| E2E | The demo script paths | Playwright | The ten demo steps |
| Load | 50 concurrent users | k6 | Against published SLOs |

**Tests that specifically carry marks** — write them deliberately and cite them in the dissertation:

- Assessment against an **unapproved rubric is refused**
- Cross-tenant read returns `404` — and a second test that **disables the app-layer check and confirms RLS still blocks it**, proving defence in depth
- Audit chain verification **detects a mutated row**
- Erasure leaves **no residue** in Postgres, MinIO or Redis
- A hallucinated citation is **rejected by the validator**
- An adversarial résumé is **flagged**
- Ingestion is **idempotent** under duplicate delivery
- A poison task reaches the **dead-letter table** without stalling the queue
- **`import-linter` fails** on a deliberately introduced cross-module import
- **No session is shared across concurrent tasks** (asserts session identity per task)

Use **`hypothesis`** for the date-interval merge that computes total experience — overlapping ranges, open-ended "present", gaps and out-of-order entries are exactly what property-based testing is for, and it is the kind of rigour that reads as senior.

Keep non-deterministic quality assertions in the eval harness, **never** in the unit suite. Fix seeds and set `temperature=0` wherever an LLM sits in a test path, or CI becomes flaky and you will start ignoring it.

## 4. CI/CD

```
PR  → ruff check + ruff format --check   (replaces black/isort/flake8)
    → mypy --strict
    → import-linter                       ← the architecture gate
    → pytest: unit + integration (testcontainers)
    → security: trivy (image + fs), pip-audit, semgrep, gitleaks, bandit
    → alembic upgrade head on a clean DB, then autogenerate --check   ← drift gate
    → build images (api + worker targets, non-root, pinned base digests)
    → eval regression gate on reduced golden set    ← the distinctive one
main → full eval suite nightly → publish report artifact
```

Two gates worth calling out in the viva:

- **The migration drift gate.** `alembic revision --autogenerate` producing a non-empty diff means a model changed without a migration. Catching that in CI is unglamorous and exactly what production experience teaches you.
- **The eval regression gate.** Faithfulness or Recall@10 more than 3% below the recorded baseline fails the build. *"You cannot merge a change that makes retrieval worse"* is a claim very few student projects can make.

Branch protection on, conventional commits, PRs even when solo — the PR history is evidence of process, and a reviewer will look at it.

## 5. Observability

- **Tracing:** OTel auto-instrumentation for FastAPI, SQLAlchemy, httpx and Redis, plus manual spans around each pipeline stage. Attributes: `tenant_id`, `correlation_id`, `model`, `prompt_version`, `tokens_in/out`, `cost_usd`, `cache_hit`. One trace should tell the whole story of one assessment.
- **Metrics:** latency per pipeline stage; retrieval candidate counts; rerank position deltas; token spend per tenant and feature; cache hit rate; queue depth and dead-letter count; ingestion success rate; circuit-breaker state.
- **Logs:** `structlog` with JSON output, correlation ID bound to a context var so every line in a request carries it automatically. **No PII or document content in logs** — assert this with a test that scans captured log output for seeded canary strings. It is the easiest compliance failure to commit by accident.
- **Dashboards:** one operational (health, queues, errors), one AI-specific (quality, cost, latency, cache hit rate). The AI-cost dashboard is unusual in student work and lands well.
- **Alerts:** dead-letter non-empty, circuit open, p95 breach, budget threshold, audit-chain break.

## 6. Security

Run the STRIDE pass in Week 14, but let its likely outputs shape the build from the start:

- **RLS as the tenancy backstop** — the application sets `app.tenant_id`; Postgres enforces it. A forgotten filter fails closed. This is a materially stronger story than app-layer filtering and worth a paragraph in the dissertation.
- Short-lived JWTs, refresh rotation, `argon2` password hashing; invitation tokens single-use and expiring
- Authorisation in one policy component, deny by default
- Upload hardening: MIME sniffing (never trust the extension), size caps, malware scan hook, **PDFs rendered, never executed**; strip active content. `PyMuPDF` parses untrusted input — keep it in the worker, not the API process, so a parser CVE cannot reach your request path
- Object storage: presigned URLs, scoped and short-lived; no public buckets
- Prompt-injection defences as a *layered* control, presented as risk reduction — not a solved problem
- LLM output is untrusted: render as text, never as HTML
- Secrets from environment only; `gitleaks` in CI; no secret in the repo, including in demo data
- Rate limits per tenant and per IP; per-tenant LLM budget caps
- `pip-audit` with a documented patch SLA; `uv.lock` committed
- TLS everywhere; security headers; strict CORS

## 7. Code conventions

- **`async` all the way down.** No sync DB calls in an async path; no blocking CPU work on the event loop — parsing, OCR and reranking run in the worker or via `run_in_executor`
- **One `session.commit()` in the codebase**, inside the UoW adapter ([02 §2](02-architecture.md#the-unit-of-work--what-replaces-transactional))
- **No nulls across ports.** Typed results or typed exceptions, never `None` as a sentinel
- Alembic migrations are append-only and reversible-by-forward-fix; never edited after merge
- OpenAPI generated from FastAPI; the web client's types generated from that OpenAPI, with drift a **build failure**, not a bug report
- Prompts live in version-controlled files with content hashes, loaded by ID — never inline f-strings
- `ruff` for lint and format, config in `pyproject.toml`, zero per-file ignores without a reason
- Comments explain *why*; the code explains *what*

## 8. Definition of done

A feature is done when: tests exist at the right level and pass · `mypy --strict` and `import-linter` are clean · it is observable (span + metric) · it is authorised and tenant-scoped (with an RLS test) · it fails gracefully · its errors are user-comprehensible · it is audited if it touches candidate data or AI output · the OpenAPI and generated client regenerate cleanly · an ADR exists if a non-obvious decision was made · the dissertation section is drafted.

Pin this list in the repo. Working against an explicit DoD — and being able to say so in the viva — is itself the senior signal.
