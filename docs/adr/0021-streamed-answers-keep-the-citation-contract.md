# ADR-0021: Streamed answers, without weakening the citation contract

- **Status:** Accepted
- **Date:** 2026-11-01

## Context

An answer from Gemini 2.5 Pro took 9 to 25 seconds, and the reader saw nothing until
it was complete. The obvious fix, streaming tokens as they arrive, collides with the
citation contract (ADR-0006). The contract says a claim is shown only after its
citations resolve, and an attempt with any unsupported claim is retried as a whole,
never shown with the bad parts removed. Tokens arrive before anything can be checked,
and a claim already on screen cannot be taken back quietly.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Stream raw tokens | Fastest first paint | Shows unvalidated text; breaks the contract outright |
| Stream claims as validated, drop failures silently | Simple | An answer with unsupported parts removed is exactly what the contract forbids |
| **Stream each claim once validated; withdraw the attempt if any claim fails** | Keeps the contract; first claim appears as soon as it is checked | A visible rewrite when a correction is needed |

## Decision

- A streaming generator emits each claim the moment its JSON object closes
  (`ClaimStreamParser` reads the schema-constrained output as it arrives). Generators
  that cannot stream are wrapped and emit their claims at the end, through the same
  events.
- `AnswerQuestion.stream` validates each claim and releases it only if its citations
  resolve. The decision is made over the whole attempt, as before. If a claim fails,
  the attempt is finished (for its usage), already-shown claims are withdrawn with a
  `withdrawn` event, and the corrected attempt streams afresh. `execute` consumes the
  same stream, so there is one implementation.
- `POST /ask/stream` sends server-sent events: `evidence`, `claim`, `withdrawn`,
  `done` (the body `/ask` returns), `error`. It runs to the first event before
  responding, so permission, budget and validation refusals stay ordinary HTTP errors.
- Answers can use a different model from judging (`GEMINI_ASK_MODEL`,
  `GEMINI_ASK_THINKING_BUDGET`). A reader waits on an answer; a judgment runs in the
  worker and decides a score.

## Rationale

The contract is preserved exactly: a finished answer only ever contains claims from an
attempt in which every claim resolved. A test scripts a generator that emits a valid
claim, then one citing a source that was never retrieved, then a correction, and
asserts the events are `evidence, claim, withdrawn, claim, claim, done` with only the
corrected claims in the answer.

## Consequences

- Provenance is written once the answer is final. A reader who disconnects mid-stream
  rolls the transaction back; nothing is recorded for an answer never delivered.
- The streaming path does not retry transient provider errors the way `generate`
  does, because claims may already be on screen; the error arrives as an `error` event.
- Streaming cannot hide a model's thinking time. With Pro, most of the wait is before
  the first token. Finding 23 measures the faster model that addresses it.

## Reversal

The buffered `/ask` endpoint is unchanged and consumes the same stream; the interface can
switch back to it in one line.
