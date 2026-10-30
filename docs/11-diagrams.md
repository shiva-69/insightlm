# 11 — Diagrams

C4 views of the system, and the data model. Sources are in `docs/diagrams/*.mmd`
(Mermaid, rendered by GitHub); SVGs for the dissertation are built with `make diagrams`.
The ER diagram is **generated from the SQLAlchemy models** (`scripts/er_diagram.py`),
so it can't drift from the schema.

## Context

Who uses InsightLM and what it depends on. The dotted line from the candidate is the
design's central risk: the people being evaluated write the input the models read
(ADR-0012).

```mermaid
flowchart LR
    recruiter(["Recruiter / hiring manager<br/>owner, recruiter, hiring manager"])
    interviewer(["Interviewer<br/>scorecards only"])
    auditor(["Auditor<br/>read-only, fairness report"])
    candidate(["Candidate<br/>writes the CV; never logs in"])

    system["<b>InsightLM</b><br/>Evidence-grounded document Q&A<br/>and hiring assessment"]

    gemini[["Gemini<br/>generation, judging, embeddings"]]
    anthropic[["Anthropic<br/>generation, judging (optional)"]]
    otel[["Grafana stack<br/>traces, metrics"]]

    recruiter -- "uploads CVs, asks, sets rubrics,<br/>moves candidates" --> system
    interviewer -- "submits scorecards" --> system
    auditor -- "verifies audit chain,<br/>reads selection rates" --> system
    candidate -. "CV text: untrusted input<br/>that models read" .-> system
    system -- "redacted, escaped evidence;<br/>metered calls" --> gemini
    system -. "same port, by configuration" .-> anthropic
    system -- "OTLP" --> otel
```

## Containers

What runs, and who may talk to whom. Only the web tier is public; the API is reachable
only from it (enforced by `deploy/k8s/networkpolicy.yaml`), which is what makes its
client-IP header trustworthy. Every limit and counter lives in Redis, so API processes
share no state and scale horizontally (docs/09).

```mermaid
flowchart TB
    browser(["Browser"])
    subgraph edge["Public"]
        web["<b>web</b><br/>Next.js 16<br/>UI + same-origin proxy;<br/>sets client IP header"]
    end
    subgraph private["Private network"]
        api["<b>api</b><br/>FastAPI, 4 uvicorn processes<br/>auth, RLS sessions, rate limits,<br/>idempotency, use cases"]
        worker["<b>worker</b><br/>ARQ<br/>outbox relay (10 s), ingestion,<br/>stalled-job reaper (60 s)"]
        pg[("<b>Postgres 17 + pgvector</b><br/>rows, vectors, tsvector;<br/>RLS per tenant;<br/>hash-chained audit log")]
        redis[("<b>Redis</b><br/>job queue, rate limits,<br/>model budget, idempotency")]
        s3[("<b>Object store</b><br/>MinIO / S3<br/>original files")]
    end
    subgraph obs["Observability"]
        otelc["OTel collector"] --> tempo[("Tempo")]
        otelc --> prom[("Prometheus")]
        grafana["Grafana"] --> tempo
        grafana --> prom
    end
    providers[["Model providers"]]

    browser -- HTTPS --> web
    web -- "/api/* (only caller)" --> api
    api -- "SQL as restricted role" --> pg
    api --> redis
    api --> s3
    api -- "outbox row, same transaction" --> pg
    worker -- "drains outbox" --> pg
    worker -- "jobs + trace context" --> redis
    worker --> s3
    api -- "judge / generate / embed" --> providers
    worker -- "embed" --> providers
    api -. OTLP .-> otelc
    worker -. OTLP .-> otelc
```

## Components

Modules and the one direction their dependencies may point, enforced by
`import-linter` in CI (nine contracts, `.importlinter`). `recruiting` sees retrieval only
through `EvidencePort`; nothing reaches another module's adapters or application layer;
the composition root is the only place that knows every module.

```mermaid
flowchart TB
    subgraph delivery["Delivery"]
        routers["api routers<br/>(HTTP schemas, error envelope)"]
        workerep["worker entrypoints"]
        cli["cli: evaluation harnesses"]
    end
    subgraph composition["Composition root"]
        bootstrap["bootstrap.py<br/>wires adapters to ports"]
        ask["ask_service.py"]
        erase["erasure.py<br/>verified saga"]
        instr["instrumented.py<br/>model-call telemetry"]
    end
    subgraph modules["Modules"]
        recruiting["<b>recruiting</b><br/>rubrics, assessment, pipeline,<br/>scorecards, fairness, talent"]
        retrieval["<b>retrieval</b><br/>chunking, embeddings, search,<br/>citation contract"]
        documents["<b>documents</b><br/>upload, parsing, hidden-text<br/>findings"]
        identity["<b>identity</b><br/>tenants, users, roles,<br/>invitations"]
        governance["<b>governance</b><br/>audit chain, redaction"]
        evaluation["<b>evaluation</b><br/>metrics, corpora, red team"]
    end
    shared["<b>shared kernel</b><br/>ids, errors, policy, ports, UoW"]

    routers --> bootstrap
    workerep --> bootstrap
    cli --> bootstrap
    bootstrap --> ask & erase & instr
    composition -- "the only place that sees every module" --> modules
    recruiting -- "EvidencePort only" --> retrieval
    retrieval --> documents
    documents --> identity
    identity --> governance
    recruiting --> governance
    evaluation -. "domain + ports only" .-> recruiting
    governance --> shared
    modules --> shared
```

## Data model

Generated. Every tenant-scoped table carries `tenant_id` and a row-level security policy
keyed on it; `audit_entries` is append-only for the application role.

```mermaid
erDiagram
    answers {
        UUID id PK
        UUID tenant_id
    }
    applications {
        UUID id PK
        UUID tenant_id
        UUID candidate_id FK
        UUID requisition_id FK
        string stage
    }
    assessments {
        UUID id PK
        UUID tenant_id
        UUID application_id FK
        UUID rubric_id FK
    }
    audit_entries {
        UUID id PK
        UUID tenant_id
    }
    candidates {
        UUID id PK
        UUID tenant_id
    }
    chunks {
        UUID id PK
        UUID tenant_id
        UUID document_version_id FK
        VECTOR embedding
    }
    dead_letters {
        UUID id PK
        UUID tenant_id
    }
    document_texts {
        UUID document_version_id PK,FK
        UUID tenant_id
    }
    document_versions {
        UUID id PK
        UUID document_id FK
        UUID tenant_id
        string status
    }
    documents {
        UUID id PK
        UUID tenant_id
    }
    ingestion_jobs {
        UUID id PK
        UUID tenant_id
        UUID document_version_id FK
        string status
    }
    invitations {
        UUID id PK
        UUID tenant_id FK
        UUID invited_by FK
        UUID accepted_by FK
    }
    memberships {
        UUID id PK
        UUID tenant_id FK
        UUID user_id FK
    }
    model_versions {
        UUID id PK
    }
    outbox_events {
        UUID id PK
        UUID tenant_id
    }
    prompt_templates {
        UUID id PK
    }
    prompt_versions {
        UUID id PK
        UUID template_id FK
    }
    requisitions {
        UUID id PK
        UUID tenant_id
        string status
    }
    rubrics {
        UUID id PK
        UUID tenant_id
        UUID requisition_id FK
        string status
    }
    scorecards {
        UUID id PK
        UUID tenant_id
        UUID application_id FK
        UUID requisition_id FK
    }
    self_identifications {
        UUID candidate_id PK,FK
        string dimension PK
        UUID tenant_id
    }
    stage_transitions {
        UUID id PK
        UUID tenant_id
        UUID application_id FK
    }
    tenants {
        UUID id PK
    }
    users {
        UUID id PK
    }
    candidates ||--o{ applications : "candidate_id"
    requisitions ||--o{ applications : "requisition_id"
    applications ||--o{ assessments : "application_id"
    rubrics ||--o{ assessments : "rubric_id"
    document_versions ||--o{ chunks : "document_version_id"
    document_versions ||--o{ document_texts : "document_version_id"
    documents ||--o{ document_versions : "document_id"
    document_versions ||--o{ ingestion_jobs : "document_version_id"
    tenants ||--o{ invitations : "tenant_id"
    users ||--o{ invitations : "invited_by"
    users ||--o{ invitations : "accepted_by"
    users ||--o{ memberships : "user_id"
    tenants ||--o{ memberships : "tenant_id"
    prompt_templates ||--o{ prompt_versions : "template_id"
    requisitions ||--o{ rubrics : "requisition_id"
    applications ||--o{ scorecards : "application_id"
    requisitions ||--o{ scorecards : "requisition_id"
    candidates ||--o{ self_identifications : "candidate_id"
    applications ||--o{ stage_transitions : "application_id"
```
