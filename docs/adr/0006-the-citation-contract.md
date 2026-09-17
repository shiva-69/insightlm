# ADR-0006: The citation contract — structured claims, validated before display

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

The project's thesis is that an AI hiring assistant should be trusted for its
*evidence*, never its conclusion. That only means something if "every claim carries a
citation" is enforced by the system rather than requested in a prompt.

The default approach — ask for markdown with `[1]` markers and parse them out — fails
in a specific way: the model produces citations that are *almost* right. It cites a
document that exists but does not support the claim, or invents a plausible page
number. Almost-right citations are worse than none, because they survive review.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Prose with inline markers, regex-extracted | Simple; familiar output | Markers drift from claims; no way to tell a real reference from a confabulated one; parsing is brittle |
| Document-level citations only | Cheap | "It's somewhere in this 12-page PDF" is not evidence a recruiter can act on |
| **Structured claims + post-generation validation** | Every claim is data with resolvable references; invalid output is caught before display | Two round trips on failure; the model must be constrained to a schema |

## Decision

1. The model is constrained by a JSON schema (`output_config.format`) and returns
   `{sufficient_evidence, claims: [{text, citations: [label]}]}`.
2. Sources enter the prompt tagged `C1`, `C2`. **The model cites tags, never names.**
3. A domain function resolves every label against the evidence *actually retrieved*.
4. Unresolvable claims are dropped, the failure is fed back, and the request is retried
   **once**. Then the answer degrades to showing the evidence without prose.
5. `sufficient_evidence: false` is a first-class outcome, not an error.

## Rationale

**Tags, not names.** Asking a model to reproduce a document title or page number
invites exactly the subtle error the contract exists to prevent. It cites an opaque
handle; resolution to a real document happens in code that cannot be wrong about it.

**Validation in the domain, not the adapter.** `retrieval/domain/answer.py` has no
network dependency, so the contract is unit-testable and cannot be skipped by a
careless adapter. `Claim` raises if constructed without a citation — there is no path
to an uncited claim, not merely a convention against one.

**One retry, not three.** A model told exactly which ids it invented either fixes it
immediately or will not. Each further attempt costs a full request for diminishing
returns.

**Degrade to evidence, never to unsupported prose.** When validation fails twice, the
user sees the retrieved passages and reads them directly. Showing a partially-grounded
answer with the bad parts silently removed would still be an answer they cannot audit.

## Consequences

**Easy:** citations are clickable because they resolve to persisted spans with
geometry. Hallucinated references are caught mechanically. Refusal is measurable, which
Phase 2's golden set depends on.

**Hard:** the generator must support structured output. Prose quality is slightly
constrained by the claim structure. A retry doubles latency in the failure case.

**We now owe:** `tests/unit/test_citation_contract.py`, which is the executable form of
this decision, and a faithfulness metric in Phase 2 that measures how often the
contract has to intervene.

## Reversal

None expected — this is the thesis. If structured output were unavailable on a target
model, the fallback is strict tool use with the same schema, which is a change of
mechanism and not of contract.
