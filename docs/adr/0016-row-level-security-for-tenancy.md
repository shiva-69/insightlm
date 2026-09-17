# ADR-0016: Tenant isolation by PostgreSQL row-level security

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Every tenant-scoped query must be filtered by tenant. The usual approach is an ORM
filter or a discipline of always writing `WHERE tenant_id = ?`. Both fail the same way:
someone forgets, once, and the system leaks another tenant's data with no error.

For a system whose entire thesis is auditability, "we filter carefully" is not an
adequate answer.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Application-layer filtering | Simple; no database features needed | Fails open. A forgotten clause is a silent leak, and no test catches the query nobody wrote |
| Schema per tenant | Strong isolation | Migration pain multiplied by tenant count; no cross-tenant analytics |
| **Row-level security** | Fails closed; enforced by the database; survives a forgotten clause | Needs a non-superuser role; policies must be written and tested |

## Decision

RLS on every tenant-scoped table, keyed on a `app.tenant_id` GUC set per transaction by
the unit of work via `SET LOCAL`.

## Rationale

The property that matters is the failure mode. With no tenant context set,
`current_tenant_id()` returns NULL, every policy comparison yields NULL, and queries
return nothing. The system fails closed. `tests/security/test_tenant_isolation.py`
demonstrates this directly: it runs `SELECT tenant_id FROM memberships` with no `WHERE`
clause at all — exactly the bug a tired developer ships — and gets back only the current
tenant's row.

`SET LOCAL` is transaction-scoped, so a pooled connection cannot carry one tenant's
context into the next request that checks it out.

### Three things this required getting right

**A restricted role.** PostgreSQL grants SUPERUSER an unconditional RLS bypass —
`FORCE ROW LEVEL SECURITY` constrains the table *owner*, but nothing constrains a
superuser. An application connecting as the default `postgres` user has no isolation at
all, however many policies exist, and the failure is silent: the policies still appear
in the catalog and every query still returns everything. Migration 0001 therefore
creates `insightlm_app` with `NOSUPERUSER NOBYPASSRLS`, and the application connects as
that role. This was found by a test that was expected to pass and did not.

**Self-service policies, not exemptions.** Two flows legitimately have a user but no
tenant: logging in, and listing the workspaces you belong to. Rather than granting them
a bypass, the policies express the real rule — *a user may always see their own
memberships, and the tenants those memberships are in*. Reads widen; writes stay
strictly tenant-scoped. The `tenants` policy consults `memberships` through a
`SECURITY DEFINER` function, because a policy that queries a table with its own policy
recurses.

**The invitation token carries its tenant.** Accepting an invitation is unauthenticated,
so there is no context to satisfy the policies. Encoding the tenant in the token
(`base64(tenant_uuid).secret`) lets the use case establish context *before* it queries,
which keeps the policies fail-closed with no special case.

**Uniqueness checks change.** Under RLS a `SELECT` cannot see a conflicting row in
another tenant, so uniqueness must be arbitrated by the constraint, not by a
pre-flight query. The unit of work translates `IntegrityError` into `ConflictError` in
one place.

## Consequences

**Easy:** a forgotten filter is harmless. Erasure and tenant-filtered vector search
inherit the same protection.

**Hard:** two roles to manage. Policies need their own tests. The relay is cross-tenant
by definition and needs a declared exemption (`app.relay`).

**Honest limitation:** RLS defends against a *forgotten* `WHERE` clause, which is the
failure that actually happens. It does not defend against application code that
deliberately lies, because the application owns the connection either way. Claiming
otherwise would overstate it.

## Reversal

Unlikely. The stronger version — a separate role holding `BYPASSRLS` with grants limited
to `outbox_events` — replaces the relay's declared exemption once the relay runs as its
own deployment. Estimated cost: half a day.
