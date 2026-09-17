# ADR-0002: PostgreSQL with pgvector as the single store

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

The system needs relational data (tenants, applications, rubrics, audit) and vector
search over document chunks. The reflexive choice is Postgres plus a dedicated vector
database.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Postgres + Qdrant/Weaviate | Purpose-built ANN, better at very high QPS | Two stores to keep consistent; chunk metadata and its embedding commit separately; tenant-filtered ANN is awkward; erasure must succeed in both |
| **Postgres + pgvector** | Chunk metadata and embedding commit atomically; RLS covers vectors too; joins against relational data; one component to operate | HNSW build times and recall tuning need care past a few million vectors |

## Decision

One PostgreSQL instance with `pgvector` (HNSW) and `tsvector`, holding rows and vectors
together.

## Rationale

Transactional consistency between a chunk, its span geometry and its embedding matters
more at this scale than ANN throughput. Three concrete wins follow from a single store:
tenant filtering happens *inside* the SQL rather than as a post-filter (a post-filter
silently shrinks recall and is one bug from a cross-tenant leak); erasure is one
transaction rather than a distributed saga with a verification problem; and hybrid
retrieval fuses lexical and dense results without a network hop.

## Consequences

**Easy:** atomic writes, RLS over vectors, one backup, one restore, one thing to run.

**Hard:** ANN tuning is ours. HNSW index builds are expensive on large tables.

**We now owe:** retrieval behind a `VectorStorePort` so the store is swappable, and an
eval harness that can prove a swap did not regress quality.

## Reversal

Trigger: sustained p95 retrieval latency breaching SLO with correct HNSW parameters,
or roughly 10M+ vectors at high QPS. Migration touches one adapter. The ablation
harness re-runs to confirm no regression. Estimated cost: two to three days.
