# 09 — SLOs and capacity

## Service level objectives

Set before the load test and encoded as its thresholds (`infra/k6/load.js`), so a run
passes or fails against them rather than being read generously afterwards.

| SLI | Objective | Why this number |
| --- | --- | --- |
| Error rate (non-4xx failures) | < 0.5% of requests | A recruiter working through a shortlist makes ~100 requests; at 0.5% they'd see an error every other session, which is the edge of tolerable |
| Read endpoints, p95 | < 300 ms | Shortlist, application, library: perceived as instant below ~300 ms |
| Ask (retrieval + generation), p95 | < 1 s offline; model-bound with a provider | With a real model the call itself is 2–15 s; the platform's share must stay small |
| Talent search, p95 | < 800 ms | One embedding call plus a vector query |
| Ingestion | a CV readable within 60 s of upload, p95 | Outbox cycle (≤10 s) plus parse, chunk, embed |
| Durability | no document left in "processing" | Permanent failures dead-letter; the reaper retries forgotten jobs |

Model latency is excluded from the platform SLOs and tracked separately
(`insightlm.model.duration` on the dashboard), because it is the provider's to meet.

## Load test

`make load`: seeds a workspace (33 CVs, assessed), then 50 concurrent recruiters for
2 minutes against it, with a traffic mix of shortlist 30%, application 20%, library 15%,
requisitions 10%, ask 15%, talent search 10%, and 0.5–1.5 s think time. Run keyless
(offline models) and with rate limits off: it measures the platform's capacity, not
Gemini's latency or the limiter.

### Results, 19 Sep 2026 (MacBook, Docker Desktop, 15 cores to the VM)

| run | requests | failed | p95 all | p95 read | p95 ask | p95 search | throughput |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 50 users, 1 API process | 6,897 | 0 | 58 ms | 62 ms | 26 ms | 33 ms | 43 req/s |
| 200 users, 1 API process | 9,315 | 0 | **1.73 s** ✗ | 1.61 s ✗ | 1.53 s ✗ | 2.41 s ✗ | 92 req/s |
| 200 users, 4 API processes | 15,567 | 0 | 115 ms | 130 ms | 65 ms | 74 ms | 154 req/s |

### The bottleneck, found rather than guessed

At 200 users with one process, `docker stats` showed the API at **100% of one core and
Postgres at 17%**. The limit was the single Python process, not the database. Four
uvicorn processes (`WEB_CONCURRENCY`, default 4 in compose) cut p95 from 1.73 s to
115 ms and brought throughput up to what 200 users with think time can offer.

Running several processes is safe because nothing depends on in-process state: rate
limits, the model budget and idempotency keys all live in Redis, and the database pool
arithmetic (4 × (10 + 5) = 60 connections) fits Postgres's default of 100.

### What the numbers do not show

- **Model-backed latency.** With Gemini, `/ask` and assessment are dominated by the
  provider (the traced embed call alone was 615 ms; generation is seconds). The platform's
  share is what's measured here.
- **Write-heavy load.** The mix is read-heavy, like a recruiter's day; bulk upload is
  bounded by ingestion throughput, which is the worker's, not the API's.
- **Production hardware.** One laptop, one VM. The ratios (the knee, the fix) transfer;
  the absolute numbers don't.
