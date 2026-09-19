# ADR-0007: Dense retrieval with contextual enrichment; hybrid RRF kept as an arm, not the default

- **Status:** Accepted (supersedes the plan's assumption that hybrid would be the default)
- **Date:** 2026-09-18

## Context

The plan assumed lexical + dense retrieval fused with reciprocal rank fusion (RRF) would
beat either alone, as it often does in the literature. CVs are short, jargon-heavy and
written in the candidate's own words; questions are written in the recruiter's. Whether
fusion helps on that distribution was an empirical question, so both were built behind
one `EvidencePort` and measured (docs/07, Findings 4–11).

## Options considered

| Option | Result on the generated corpus (Gemini embeddings) |
| --- | --- |
| B: dense only | nDCG 0.469: passages about achievements didn't name whose they were |
| **C: dense + contextual enrichment** (document title prepended before embedding, not cited) | **nDCG 1.000**, R@8 1.000, MRR 1.000 |
| D: hybrid RRF (C + OR-ranked lexical) | nDCG 0.811 once phrasing varied realistically |
| L: lexical only | nDCG 0.457 |

## Decision

Production retrieval is **C: dense, over chunks embedded with their document's title as
context**. Hybrid RRF (D) and lexical (L) stay implemented as `RetrievalStrategy` arms,
selectable per query and measured in every evaluation run; the CI gate runs D so the
lexical path can't rot unnoticed.

## Rationale

Contextual enrichment fixed a whole class of failure (a passage about an achievement
never names the person, because the name is in another chunk), taking nDCG from 0.47
to 1.0. Fusion, by contrast, was neutral when both arms were strong and harmful once
paraphrase was realistic: unweighted RRF gives a weak arm an equal vote, so a degraded
lexical ranking pulled a perfect dense ranking down. Two findings showed the same
mechanism with the arms reversed. The null and negative results are kept in the report
on purpose; one of them (Finding 5) turned out to be a bug in the lexical arm, which is
why a result is re-examined before it's believed.

## Consequences

- One query per retrieval instead of two.
- Exact-token needs (a certification number, an unusual acronym) are the known weakness
  of dense retrieval; the lexical arm exists for them, and a weighted fusion is the
  route if real usage shows the need.
- The benchmark saturates at 40 synthetic documents; the conclusion is "no evidence
  fusion helps here", not "fusion never helps".

## Reversal

Revisit with weighted fusion (lexical weight < 1) or learned fusion if a larger, real
query log shows exact-term queries dense retrieval misses. Switching is a configuration
change: the arms and the harness already exist.
