# ADR-0011: Deterministic aggregation in code; the model never emits a score

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

With per-criterion verdicts (ADR-0010), something still has to turn them into a ranking
and decide which applications a person must look at. Whoever does this effectively owns
the hiring decision the system suggests. So it has to be reproducible, explainable line
by line, and incapable of rejecting anyone.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Model returns an overall score alongside the verdicts | "Holistic" | Not reproducible; can contradict its own verdicts; unexplainable; a model setting the bar is exactly what the approval gate forbids |
| Model ranks candidates against each other | Captures relative strength | Order-sensitive, non-deterministic, and couples every candidate's result to every other's |
| **Pure function over verdicts and rubric weights** | Same input, same output; every point traceable to a criterion; testable without a model | No holistic judgment. That judgment belongs to a person, which is the point |

## Decision

`recruiting.domain.assessment.aggregate(rubric, judgments)`:

- **Score** = `100 × Σ(weightᵢ × valueᵢ) / Σ weightᵢ`, with verdict values
  strong 1.0, adequate 0.66, weak 0.33, absent 0. Rounded to one decimal.
- It **refuses** an unapproved rubric and a set of judgments that doesn't cover every
  criterion (both are `InvariantViolationError`: bugs, not user errors).
- **Flags**, never rejections:
  - `missing_must_have`: a must-have criterion is `absent`;
  - `disqualifying_absent`: a criterion marked "flag for review if absent" is `absent`;
  - `low_confidence`: mean judgment confidence is below 0.55.
- Any flag sets `needs_human_review`.

The rubric itself is gated by a human. A draft becomes usable only when approved, and not
by the person who proposed it. The one exception is the workspace owner, because a team
of one has nobody else to ask, and a rule that makes the product unusable for a founder
hiring their first engineer is a rule that gets bypassed. Approving a new version
supersedes the old one. Every assessment records the rubric version it ran under.

## Rationale

`absent` and `weak` are kept apart because they are different claims. `weak` means
"the evidence shows a little of this". `absent` means "nothing retrieved speaks to this",
which may mean the candidate lacks it, or the PDF parsed badly, or the CV says it in
words retrieval didn't match. Only a person reading the document can tell those apart.
So `absent` on a must-have produces a flag and a lower score, and it never removes
anyone from the list.

## Consequences

- Scores are reproducible from stored judgments, so "why 62?" is answered by
  arithmetic shown in the UI, not by asking the model again.
- Changing the weights or verdict values changes every future score, and no past one.
  Past assessments keep their numbers and their rubric version.
- The unit suite asserts the arithmetic, the ordering independence, the refusals, and
  that `weak` outscores `absent` while only `absent` raises the must-have flag.

## Reversal

If the organisations using this want learned weights (e.g. from hiring outcomes), they
would replace the constant verdict values with a fitted mapping, still in code, still
versioned with the rubric. We would not revisit the rule that a model never emits the
score. That rule is the thesis, not a tuning choice.
