# ADR-0019: Verticals declare their own roles, audit actions and screening rules

- **Status:** Accepted
- **Date:** 2026-10-30

## Context

The claim that recruiting is one vertical on a general document engine held at the
evidence seam (`EvidencePort`) but nowhere else. Hiring vocabulary lived in modules
every vertical shares:

- `shared.domain.roles` hard-coded the six hiring roles and their permissions;
- `governance.domain.audit.AuditAction` was one closed enum including every recruiting
  event;
- `documents.domain.findings` scanned for "rate this candidate" and "this applicant is a
  perfect match";
- `governance.domain.redaction` defined the `blind-v1` profile, institutions and
  demographic lines included.

A second vertical would have had to edit all four. The flat `Container` (about 45
fields) and an application layer that imported a kernel adapter (`UnmeteredBudget`) made
the same point less visibly.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Leave it; edit the shared modules per vertical | No work now | The engine stops being one: every vertical changes code every other vertical depends on |
| Global registries populated at import time | Small diff | Correctness depends on import order; a test importing only the kernel sees an empty registry |
| **Value types in the kernel; declarations in each module; one assembly in the composition root** | Explicit, testable, fails at startup on a mistake | Touches every call site once |

## Decision

- `Permission`, `Role` and `AuditAction` become small value types. The kernel declares only
  the platform's own: the owner, the auditor, and member and audit permissions.
- Each module declares its vocabulary next to its code: `documents.domain.permissions`,
  `recruiting.domain.roles`, `identity.domain.audit_actions`,
  `recruiting.domain.audit_actions`, and so on.
- `RoleCatalogue`, built in `bootstrap.build_role_catalogue`, resolves each role's
  permissions. The owner gets every declared permission and the auditor every
  read-only one, so a vertical extends both without anyone remembering to.
- `Actor` carries its resolved permission set, so a policy check needs no global table.
- `InstructionScanner` holds the engine's injection patterns; recruiting extends it with
  hiring phrases (`recruiting.domain.screening`), wired in the composition root.
- Redaction is an ordered list of rules. Governance supplies the person-level ones
  (names, contact details, pronouns); recruiting adds institutions and demographic lines
  and assembles `blind-v1`.
- The container is grouped by module. Three new import-linter contracts keep it that
  way: the kernel is layered, nothing below the composition root imports a kernel
  adapter, and no module depends on the composition layer.

## Rationale

The test that matters is in `tests/unit/test_policy.py`: a contract-review role with its
own permissions is declared and resolved without editing the kernel, documents or
recruiting. The refactor changed nobody's behaviour, and each part was checked against
what it replaced: the auditor's permissions are pinned; all 21 audit action strings are
identical, so existing hash chains still verify; the 11 injection patterns are the same
set; and `blind-v1` produces byte-identical output on 60 CVs.

## Consequences

- Adding a vertical means adding its declarations to `bootstrap` and nowhere shared.
- Roles are no longer an enum in the API schema; the router resolves names through the
  catalogue, and `GET /members/roles` lists what the running system knows.
- A role or permission declared twice, or a grant of an undeclared permission, stops the
  application at startup rather than failing silently later.

## Reversal

Low cost. The value types could be replaced by enums again by concatenating the
declarations into one module; nothing persisted changes, because roles and actions are
stored as their strings.
