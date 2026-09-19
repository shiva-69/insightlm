# ADR-0009: Cache in the evaluation harness, never in the product's judgments

- **Status:** Accepted (replaces the plan's semantic cache in Redis)
- **Date:** 2026-09-19

## Context

Two costs pushed towards caching: evaluation runs that re-embedded the same corpus and
re-asked the same judge questions (the first ablation made ~400 paid calls, most of them
waste), and production answers to repeated questions. The plan proposed a semantic cache
(similar questions served the same answer) in Redis.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Semantic answer cache in production | Fewer paid calls for repeated questions | A near-match serves an answer to a different question; answers must reflect the current documents; a cached assessment isn't a fresh reading of the evidence |
| Exact-match cache in production | Safe on identity | Low hit rate on free-text questions; still needs invalidation on every document change |
| **Content-addressed caches in the evaluation harness only** | Reruns are free; nothing user-facing is ever stale | Production pays for every call |

## Decision

- **Embedding cache** (SQLite, keyed by model, dimensions, input kind and text hash):
  the evaluation harness only.
- **Judgment cache** (keyed by model, prompt version, criterion and the *rendered*
  context, plus an optional salt for repeated samples): the evaluation harness only.
- **No answer or assessment cache in the product.** Spend is controlled by the
  per-workspace model budget (docs/10) and by skipping paid calls when evidence is empty.

## Rationale

In hiring, a served answer that doesn't match the question, or an assessment that isn't
a fresh reading of the candidate's documents, is a correctness and fairness problem,
not a latency win. In evaluation, the opposite holds: identical inputs should cost
nothing twice, and keying on the rendered prompt context means a change to how sources
are rendered is correctly treated as a new question (that key choice was made after the
escaping change in Finding 14 would otherwise have served stale verdicts).

## Consequences

- Evaluation reruns are free until a case, prompt, rendering or model changes; that made
  ~550 paid calls across the project affordable and repeatable.
- Production cost scales with use; the budget and the dashboard's spend panel are the
  controls.

## Reversal

An exact-match cache for `/ask`, invalidated by a workspace-level corpus version, is the
safe first step if real usage shows many identical repeated questions. A semantic cache
would need a measured false-match rate before it touched anything a recruiter reads.
