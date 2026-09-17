# ADR-0001: A single Python service, structured hexagonally

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

InsightLM has two halves: a document-intelligence engine and a recruiting vertical
built on it. The engine's work — layout parsing, OCR, embeddings, cross-encoder
reranking, RAGAS-style evaluation — is Python-native. The recruiting domain is
transactionally coupled: a stage transition writes a candidate row, an audit entry, an
outbox event and an SLA timer, and those must commit or fail together.

The author's professional depth is Spring Boot. The initial design therefore proposed
a Java core with a Python ML sidecar.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Java core + Python sidecar | Transactional core in the author's strongest stack; `@Transactional`, Spring Security | Contract tests, DTOs defined twice, cross-language debugging — roughly 2 weeks of a 16-week solo budget, spent on infrastructure that is not the contribution |
| Java only | One language | Fights the ecosystem exactly where the dissertation's contribution lives; layout models have no real Java equivalent |
| **Python only, hexagonal** | One ecosystem, one CI path, one debugger; effort goes to the eval harness | No `@Transactional`; async session discipline is a real footgun; boundaries need enforcing by tooling |

## Decision

A single Python service. Each module is split `domain / ports / application / adapters`;
modules reach each other only through `Protocol` ports; `import-linter` fails the build
on violations.

## Rationale

The dissertation's contribution is the evaluation harness and the ablation study, and
that work is Python-shaped. A cross-language split taxes the infrastructure around the
contribution rather than the contribution itself. Meanwhile the *boundaries* that
actually matter — the ones that make "recruiting is a vertical on the document engine"
true — are internal, and enforcing them with `import-linter` is strictly cheaper than
enforcing them with a network.

Restraint here is also the honest answer to "why not microservices": there is no
independent scaling requirement, no separate team, and no differing availability need.
A service boundary would buy distributed-transaction problems and nothing else.

## Consequences

**Easy:** one deployment, one test suite, atomic transactions across the whole domain,
direct debugging.

**Hard:** the unit of work must be hand-built and hand-enforced (ADR-0005). Async
session discipline is on us — a session shared across `asyncio.gather` corrupts.

**We now owe:** `.importlinter` kept current, `mypy --strict` kept clean, and an
explicit unit-of-work contract with tests.

## Reversal

The extraction order, should it ever be needed, is: worker → retrieval → documents,
with `recruiting` staying the transactional core. The hexagonal structure is what makes
this cheap: an adapter behind an existing port becomes an HTTP client and no
application code changes. Trigger would be a genuine independent-scaling need —
sustained worker saturation while the API is idle. Estimated cost: days, not weeks,
precisely because the ports already exist.
