# ADR-0003: In-application JWT authentication, not Keycloak

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

The system needs multi-tenant authentication with six roles, team invitations, and
per-tenant scoping. It does not need SSO, SAML, or social login.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Keycloak | Batteries included; SSO if ever needed | A large operational dependency for one tenant model; the auth flow becomes something configured rather than understood |
| **In-app JWT + argon2** | Small, readable, fully explicable; no extra container | Everything is ours to get right, including rotation and revocation |

## Decision

`PyJWT` HS256 access tokens with a 15-minute TTL, `argon2id` password hashing, and
single-use expiring invitation tokens.

## Rationale

The deciding factor is examinability. Authentication is the part of the system an
examiner is most likely to ask about line by line, and "Keycloak handles it" is a
weaker answer than being able to walk through issuance, verification and the
authorisation decision. The scope genuinely does not need a federated identity
provider, and adopting one to look production-grade would be cargo cult.

Tokens carry `tenant_id` and `role`, so authorisation needs no database round trip on
the hot path.

## Consequences

**Easy:** no extra service; the whole flow reads top to bottom; tests are fast.

**Hard:** a role change takes effect only at the next token issue. At a 15-minute TTL
this is an acceptable staleness window, and it is a deliberate trade rather than an
oversight. Revocation before expiry would need a denylist.

**We now owe:** argon2 parameters reviewed as hardware moves (`needs_rehash` is wired
in), and a refresh-token rotation story before any real deployment.

## Reversal

Trigger: a requirement for SSO, SAML or corporate directory integration. Migration
means moving issuance and verification behind the existing `TokenIssuer` port and
pointing it at an OIDC provider; the `Actor`/policy layer is unaffected because it
already depends only on the port. Estimated cost: three to five days.
