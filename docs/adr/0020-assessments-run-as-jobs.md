# ADR-0020: Assessments run as jobs in the worker

- **Status:** Accepted
- **Date:** 2026-10-30

## Context

An assessment makes one model call per criterion and took 12 to 15 seconds with
Gemini 2.5 Pro, all inside the HTTP request. Bulk intake ran from the browser: upload,
poll until the CV was read, add the candidate, assess, three at a time, so a batch of
fifty depended on a tab staying open for several minutes. A slow provider held requests
open long enough to look like an outage, and there was no way to re-assess a
requisition after its rubric changed.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Keep it synchronous; raise timeouts | No new moving parts | The browser still drives the batch; every slow call is a held connection |
| Background task in the API process (`asyncio.create_task`) | Small | Lost on restart; no retries; nothing records that it was asked for |
| **A job row plus an outbox event, run by the ARQ worker** | Durable, observable, retried, reuses the ingestion machinery | A table, a task, a reaper rule |

## Decision

- `POST /applications/{id}/assessments` checks the permission, the application and the
  approved rubric, then commits an `assessment_jobs` row and an `assessment.requested`
  outbox event in one transaction, and returns 202 with the job. A second request for an
  application with an active job joins it.
- The worker claims a QUEUED job, waits for the candidate's documents to be read (10 s
  checks, without spending an attempt, for up to 30 minutes), and runs the assessment as
  the requester with the role they held when they asked.
- Provider errors are retried with backoff (5 s, then 10 s; three attempts). Refusals —
  no approved rubric, no permission, budget spent — fail at once with their reason.
- The reaper returns jobs queued for 5 minutes or running for 15 to the queue.
- `POST /requisitions/{id}/assessments` queues a batch: the unassessed, the stale
  (assessed under an older rubric version), or all. Hired and rejected applications
  are skipped.
- `?wait=true` runs the same job inline and returns the assessment, for scripts and
  tests. A refusal surfaces as its real status (409, 403, 429).

## Rationale

The outbox already makes "a change and the work it causes" atomic for uploads; doing the
same for assessments means a requested assessment can never be lost between the
database and the queue. Waiting for readiness on the server is what lets the browser stop
after uploading: an assessment queued before its CV is read waits for it, instead of
judging an empty document and marking every criterion absent. Verified live: a CV
queued immediately after upload waited one 10-second cycle and scored 100 with three
cited verdicts.

## Consequences

- The shortlist reports each application's latest job and whether its score is stale,
  and the UI polls while any job is active.
- Re-assessing after a rubric change is explicit (a button), not automatic, so changing
  criteria never spends model budget by surprise.
- A role change takes effect for jobs requested afterwards: the same trade-off as token
  TTLs in ADR-0003.

## Reversal

`?wait=true` already runs the job inline; making it the default restores synchronous
behaviour without removing anything.
