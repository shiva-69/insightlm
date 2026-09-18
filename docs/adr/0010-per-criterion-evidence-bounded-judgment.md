# ADR-0010: Per-criterion, evidence-bounded judgment over whole-résumé scoring

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

An assessment has to answer "why did this candidate get this result?" one line at a
time, in front of a hiring manager or a regulator. The obvious design, which is to put
the whole résumé and the whole rubric in one prompt and ask for a score, can't do that.
The model sees everything at once, weighs things nobody asked it to weigh (a school
name, a gap year, a name), and returns a number whose reasons are whatever rationale
it writes afterwards.

We also want the recruiting module to depend on the document engine through its port
alone (ADR-0001, the `module-internals-are-private` contract), and each judgment to be
checked the same way the citation contract (ADR-0006) checks answers.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Whole résumé + whole rubric, one call, one score | One call; simplest | Unexplainable; the model sees irrelevant attributes; one bad parse breaks everything; a score with no evidence |
| Whole résumé, one call, structured per-criterion output | One call; per-criterion structure | The model still sees the whole document for every judgment, so criteria bleed into each other; citations drift across criteria |
| **Retrieve per criterion, judge each criterion on its own evidence** | Each verdict can see only its own evidence; citations resolve against a known set; criteria are independent and parallel | N calls per candidate; retrieval quality now caps assessment quality |

## Decision

For each criterion in the approved rubric:

1. Retrieve up to 5 passages **from this candidate's documents only** (`document_ids`
   scope on the `EvidenceQuery`), using the criterion label plus its evidence hint as
   the query. Dense strategy, per Finding 11.
2. Ask a `CriterionJudge` for `{verdict ∈ strong|adequate|weak|absent, confidence,
   rationale, citations[C1..]}`, with only those passages in the prompt.
3. Resolve citation labels against the retrieved passages. A non-`absent` verdict that
   cites nothing retrieved is **downgraded to `absent`**, with the reason recorded. It is
   not kept at lower confidence.
4. Snapshot each cited passage (quote, document title, page geometry) into the
   assessment, so it still shows exactly what it rested on after the document is
   re-ingested.

Retrieval for all criteria runs in one session. Judging then runs through
`asyncio.gather` under a semaphore of 4 and **holds no database session**, so a slow
model can't exhaust the connection pool. When retrieval returns nothing, the paid judges
skip the call and return `absent`.

## Rationale

The dominant force is explainability. With judgments isolated per criterion, the score
is only as mysterious as its most mysterious verdict, and every verdict points at the
text it rests on. Isolation is also a fairness control: the judge for "Kubernetes in
production" never sees the candidate's name or university unless those words happen to
be in a Kubernetes passage.

## Consequences

- Cost is N judge calls per candidate (typically 3–8). The offline judge makes it free
  for tests and demos. Empty evidence costs nothing.
- **Assessment quality is now bounded by retrieval quality.** A résumé that parses badly
  produces `absent`, which is why `absent` is a flag for a person, never a rejection
  (ADR-0011).
- `recruiting` depends on `retrieval.ports.EvidencePort` and the evidence types, nothing
  else. The composition root hands the port over (`EvidenceProviderAdapter`).
- The offline baseline judge (term overlap) can't read negation. This is pinned by a
  test and stated in the UI, so the baseline is never mistaken for a judge.

## Reversal

If long-context models make whole-document judgments measurably better on the
evaluation set *and* we can still bind every verdict to cited spans, we could batch
criteria into one call per candidate. The domain would be unchanged. It's a new
`CriterionJudge` adapter that answers several criteria at once, and `_resolve` still
checks every citation. We'd revisit on evidence from the harness, not on per-call cost
alone.
