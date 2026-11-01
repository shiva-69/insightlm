# ADR-0022: Contract review as a second vertical, built without touching the engine

- **Status:** Accepted
- **Date:** 2026-11-01

## Context

The project's central structural claim is that recruiting is one application of a
general document engine. Until now that claim rested on one vertical and an
import-linter contract. A second vertical is the direct test: if the engine is general,
a new application should need no change to it.

## Decision

Build contract review as `insightlm.contracts`, with the same shape as recruiting and no
dependency on it:

- **Domain.** Playbooks of positions (clause, standard, severity), approved by someone
  other than their author and immutable once approved. Reviews judge each position as
  meets, deviates or missing; a meets or deviates finding must cite. The risk summary
  and the "needs counsel" decision are computed in code from the findings and each
  position's severity.
- **Vocabulary.** Its own roles (contract manager, counsel), permissions, audit actions,
  injection phrases and identifiers, declared in the module and assembled by the
  composition root (ADR-0019).
- **Engine use.** Evidence through `EvidencePort`, audit through the governance unit of
  work, isolation through row-level security, budgets through `ModelBudget`.
- **Boundary.** The verticals share one layer in the import-linter dependency contract and
  are marked independent: neither may import the other. The rule was checked by adding a
  deliberate violation, which broke the build, before removing it.

## Rationale

The acceptance test is a diff: against `main`, `documents`, `retrieval`, `governance`,
`shared`, `identity` and `recruiting` have no changes at all. The vertical touched only
its own module, the composition layer, delivery (an API router, a CLI), a migration,
tests and the web app. Two things the earlier refactor was for showed up without being
asked: the auditor gained read access to reviews automatically, and the Team page offered
the new roles because it now reads them from the catalogue.

## Consequences

- A third vertical follows the same path: a module, its declarations in `bootstrap`, a
  router, a migration.
- Contract review runs synchronously; a contract has a handful of positions and the same
  job machinery recruiting uses (ADR-0020) can be adopted if reviews grow.
- The shared kernel still carries recruiting's identifier types from before ADR-0019. New
  verticals declare their own; moving recruiting's is optional tidying.

## Reversal

Delete the module, its declarations in `bootstrap`, its router and migration 0010. Nothing
else refers to it.
