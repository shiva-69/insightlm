# ADR-0008: No cross-encoder reranker until the benchmark can show it helping

- **Status:** Accepted (the plan's reranker is deferred, deliberately)
- **Date:** 2026-09-19

## Context

The plan put a cross-encoder reranker behind a `RerankPort` in Phase 3. A reranker
re-scores the top-k passages with a model that reads query and passage together; it
usually improves precision at the cost of latency (tens to hundreds of ms per query on
CPU) and a model to host.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Local ONNX cross-encoder in the worker/API | No per-call cost | Model to ship and host; CPU latency on every query |
| Hosted rerank API | No hosting | Per-call cost; another provider and another place CV text is sent |
| **Defer, with the seam in place** | Nothing to run or pay for | Must be revisited when the evidence changes |

## Decision

No reranker now. Retrieval over-fetches (top-k wider than returned) and the
`EvidencePort` is where a reranker would slot in, so adding one is a new adapter, not a
redesign.

## Rationale

The measured configuration already ranks the right passage first for every answerable
item (MRR 1.000, Finding 11), and assessment retrieves at most five passages from a
single candidate's CV, where there is little left to reorder. A reranker would add
latency, a model and, if hosted, another processor of candidate data, to improve a
number that is already at its ceiling. The honest statement is that this benchmark
can't show a reranker helping, so its cost isn't justified by evidence yet.

## Consequences

- Lower latency and one fewer model in the data path.
- The evaluation harness would show a reranker's effect as a new arm the day one is added.

## Reversal

Add one when a harder benchmark (longer CVs, multi-document candidates, a real query
log) shows MRR or nDCG below ceiling for dense retrieval. The judge-v2 finding that
evidence buried in long passages is harder is the likeliest trigger.
