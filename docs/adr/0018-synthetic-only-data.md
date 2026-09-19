# ADR-0018: Synthetic data only, as an ethics position with a method

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Evaluating a hiring system needs CVs, and real CVs are personal data about people who
never agreed to be test material. Public CV datasets exist, but their provenance and
consent are usually unclear, and they bake in whatever demographic skew their sources
had.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Scraped or public CV datasets | Realistic | Consent unclear; special-category data; uncontrolled skew; can't be published with the project |
| Real CVs from volunteers | Consent possible | Small, skewed, and still personal data to secure and erase |
| **Synthetic CVs generated from known facts** | No one's data; ground truth known by construction; every experiment reproducible and publishable | Less messy than reality; results must be framed as such |

## Decision

Every document used in development, testing and evaluation is generated:
`seed-v1`/generated corpora for retrieval, `judge-v1/v2` for the judge, `redteam-v1` for
injection, `fairness-v1` for counterfactuals, `cohort-v1` for the 50-CV shortlist check.
Each is deterministic (byte-identical PDFs for a seed), and each labels its truth: which
passage supports which fact, which verdicts are acceptable, which candidate has which
skill.

## Rationale

Knowing the truth by construction is what turns demos into measurements: the shortlist
can be checked against known strength (Spearman ρ 0.86), a hidden payload's effect can be
isolated, and a counterfactual can change exactly one attribute. It also means the whole
evaluation can be published and rerun by anyone.

## Consequences

- **The known cost:** synthetic text is cleaner than real CVs. Finding 10 is the example:
  a template reused verbatim across forty CVs made retrieval look worse than it was, and
  was only caught because the result was inspected, not accepted. Every finding is
  framed as "on this corpus".
- Names in fixtures are common names chosen to vary perceived gender and region; they
  are test inputs, not people.

## Reversal

A consented, access-controlled real-CV study would be the next step for external
validity, with erasure and retention applied to it exactly as in the product (ADR-0015).
