# Architecture Decision Records

ADRs are the highest-leverage document in this project. They are the artifact that most clearly separates "a student who built something" from "an engineer who made decisions" — and they become your design chapter with almost no rewriting.

**Rule:** write the ADR *when you make the decision*, not retrospectively. A reconstructed ADR reads like a justification; a contemporaneous one reads like engineering. Keep them short — one page is plenty.

## Template — `docs/adr/NNNN-short-title.md`

```markdown
# ADR-NNNN: <decision in one line>

- **Status:** Proposed | Accepted | Superseded by ADR-NNNN
- **Date:** YYYY-MM-DD
- **Context:** the forces at play — constraints, requirements, what we do not know yet.
- **Options considered:**
  | Option | Pros | Cons |
- **Decision:** what we chose, stated plainly.
- **Rationale:** why, against the forces above. Which force dominated.
- **Consequences:** what this makes easy; what it makes hard; what we now owe.
- **Reversal:** what would make us revisit, and what the migration would cost.
```

The **Reversal** section is the part reviewers notice. Anyone can justify a choice; knowing its expiry date is the senior move.

## Records

Written records link to their file. The rest are placeholders for decisions not yet
made — listed so the vocabulary is designed rather than accreted.

| # | Decision | Phase |
| --- | --- | --- |
| [0001](0001-single-python-service-hexagonal.md) | Single Python service with a hexagonal structure, not a polyglot split | 0 |
| [0002](0002-postgres-pgvector-single-store.md) | PostgreSQL + pgvector as the single store for rows and vectors | 0 |
| [0003](0003-in-app-jwt-auth.md) | In-app JWT auth rather than Keycloak | 0 |
| [0004](0004-structure-aware-chunking.md) | Structure-aware chunking with persisted geometry spans | 1 |
| [0005](0005-unit-of-work-contract.md) | An explicit unit of work, with exactly one commit site | 0 |
| [0006](0006-the-citation-contract.md) | The citation contract: structured claims, validated before display | 1 |
| [0007](0007-dense-retrieval-with-contextual-enrichment.md) | Dense retrieval with contextual enrichment; hybrid RRF kept as an arm, not the default | 3 |
| [0008](0008-no-reranker-until-it-earns-its-place.md) | No cross-encoder reranker until the benchmark can show it helping | 3 |
| [0009](0009-caching-only-in-evaluation.md) | Cache in the evaluation harness, never in the product's judgments | 3 |
| [0010](0010-per-criterion-evidence-bounded-judgment.md) | Per-criterion evidence-bounded judgment over whole-résumé scoring | 4 |
| [0011](0011-deterministic-aggregation.md) | Deterministic aggregation in code; the model never emits a score | 4 |
| [0012](0012-layered-injection-defence.md) | Layered prompt-injection defence, and why no single layer suffices | 6 |
| [0013](0013-read-time-redaction.md) | Read-time redaction for blind screening, not write-time | 6 |
| [0014](0014-arq-and-transactional-outbox.md) | ARQ on Redis + transactional outbox, rather than Celery or a broker | 0 |
| [0015](0015-verified-erasure-saga.md) | Erasure as a verified saga across all stores | 6 |
| [0016](0016-row-level-security-for-tenancy.md) | Postgres RLS for tenancy rather than application-layer filtering | 0 |
| [0017](0017-direct-sdks-behind-ports.md) | Provider SDKs directly, behind ports, not an LLM framework | 1 |
| [0018](0018-synthetic-only-data.md) | Synthetic data only, as an ethics position with a method | 2 |
| [0019](0019-verticals-declare-their-own-vocabulary.md) | Verticals declare their own roles, audit actions and screening rules | 9 |
| [0020](0020-assessments-run-as-jobs.md) | Assessments run as jobs in the worker | 9 |
| [0021](0021-streamed-answers-keep-the-citation-contract.md) | Streamed answers, without weakening the citation contract | 9 |

Add records as real decisions arise. Superseded ADRs stay in the repo with their status updated — **the trail of a changed mind is evidence of engineering judgment, not of error.**
