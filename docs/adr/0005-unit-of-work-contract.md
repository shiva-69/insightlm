# ADR-0005: An explicit unit of work, with exactly one commit site

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

ADR-0001 chose Python, which means giving up Spring's `@Transactional`. Transaction
boundaries become something the codebase has to define for itself. SQLAlchemy's async
session makes two failure modes easy: committing from inside a service method (so no
single place knows what the transaction contains), and sharing a session across
`asyncio.gather` (which corrupts state in ways that surface as impossible-looking bugs).

Phase 4 fans assessment out across rubric criteria concurrently, so the second failure
mode is not hypothetical — it is scheduled.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Session passed around directly | Least ceremony | No boundary; commits appear anywhere; nothing to test |
| Decorator mimicking `@Transactional` | Familiar | Hides the boundary; interacts badly with async context |
| **Explicit `UnitOfWork` async context manager** | The boundary is visible at every call site; testable; carries tenant context | More typing at each use case |

## Decision

A `UnitOfWork` Protocol implemented by one SQLAlchemy adapter, with these rules:

1. One unit of work per request or task, opened at the **use-case** boundary.
2. `session.commit()` appears in **exactly one file** — the adapter.
3. Exiting without `commit()` rolls back. There is no implicit commit.
4. Repositories receive a session; they never create or commit one.
5. Concurrent fan-out gets its own unit of work per task, or does no database I/O.
6. The unit of work also sets the RLS context (ADR-0016) and owns the outbox.

## Rationale

Making the boundary an object rather than a decorator means you can see it. Rule 3 in
particular converts a silent bug (forgetting to commit, losing work) into an obvious
one. Putting the audit log and the outbox *on* the unit of work is what makes "every
identity change is audited" and "events publish with their write" structural rather
than aspirational.

Protocol members are declared as read-only properties, not attributes: a mutable
Protocol attribute is invariant, so an adapter exposing a concrete repository would not
satisfy a Protocol demanding the abstract one.

## Consequences

**Easy:** atomicity is obvious at a glance; RLS context cannot be forgotten; tests can
assert rollback behaviour directly.

**Hard:** every use case carries `async with`. Nested units of work are refused rather
than silently joined, which occasionally forces a restructure — correctly.

**We now owe:** the tests in `tests/integration/test_unit_of_work.py`, which pin all six
rules including the concurrency one.

## Reversal

None expected. If the codebase ever moved to a framework with declarative transactions,
the port would stay and the adapter would change.
