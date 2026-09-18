# ADR-0015: Erasure as a verified saga across all stores

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

A candidate's data lives in four modules and two stores. Foreign-key cascades reach
some of it; saved answers that quote a CV, pipeline events that name its versions, and
the files in object storage are reached by none. The audit log is append-only and
hash-chained, so it can't be edited at all. "We deleted it" needs to be something the
system proves, not something it asserts.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| `DELETE FROM candidates` and trust the cascades | Simple | Leaves answers, files, events; nothing checks |
| Per-module erasure handlers via events | Decoupled | Eventual; no single place knows it finished; still no verification |
| **One saga in the composition layer, then a residue search** | Knows every store; ordered; verifiable in the same call | The one component that knows every module's tables |

## Decision

`EraseCandidate` runs under the workspace's RLS context (so a bug can't cross
workspaces) in a fixed order: collect fingerprints; delete answers citing or naming the
candidate; delete files; delete documents (cascading to text, chunks, embeddings); delete
outbox and dead-letter events naming the versions; delete the candidate (cascading to
applications, assessments, scorecards, stage history, self-identification) **last**, so
a re-run after a crash still has the name. Then **verify**: search every text, JSON and
UUID column of every workspace table for the name, email, ids and three verbatim CV
snippets, and check every file key is gone. Documents shared with another candidate are
kept and reported.

The audit log is searched too. It may keep bare ids as tombstones (that an upload
happened and was erased is what an audit trail is for) and never names, emails or text.
Upload entries no longer record the document title or checksum.

## Consequences

- A negative-control test plants the name in a recruiter's note and asserts verification
  reports it, so the search is known to find things.
- Verification found the document id in the audit log on first run; that forced the
  tombstone rule to be stated rather than implied.
- Retention reuses the saga, so automatic deletion is verified the same way.
- Erasure is synchronous. At this scale it takes about a second; a large workspace would
  move it to the worker with the saga's step state persisted.

## Reversal

If we added a store (a vector database, a search cluster, a warehouse), it would need a
delete step *and* a verification step here before it held candidate data. The rule that
nothing identifying enters the audit log is permanent.
