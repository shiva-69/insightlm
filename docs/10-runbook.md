# 10 — Runbook

For whoever is on call. Each section is a symptom, where to look, and what to do. The
dashboard is **Grafana → InsightLM → overview** (`localhost:3001` in compose); every
error response carries a `correlation_id` that finds its log lines and its trace.

## Deploy

1. Build and push the three images (`api`, `worker`, `web`) at the same tag.
2. Run migrations **first**, as the privileged owner:
   `kubectl apply -f deploy/k8s/migrate-job.yaml && kubectl -n insightlm wait --for=condition=complete job/insightlm-migrate --timeout=5m`.
   Migrations are additive where possible; a failed Job means **don't roll out**.
3. `kubectl apply -k deploy/k8s`. The API rolls with `maxUnavailable: 0`; workers get
   120 s to finish a step, and anything cut off is re-run by the reaper.
4. Check the dashboard's 5xx ratio and p95 for ten minutes before calling it done.

Rollback: re-apply the previous tag. Schema changes are backward compatible for one
release, so the previous code runs on the new schema.

## Following one request

- From a user's error: the `correlation_id` in the response. Logs are JSON with that
  field; `kubectl logs -l app.kubernetes.io/name=insightlm | grep <id>`.
- From a slow request: Grafana → Explore → Tempo, search `{resource.service.name="insightlm-api"}`.
  An upload's trace continues into the worker (`ingest_document_version`, then one span
  per step, then `gen_ai.*` for the provider call).

## Symptoms

### 5xx ratio above 0.5%
- **Look:** dashboard "Requests by status"; logs with `unhandled_error` (the handler
  logs the type, never the message, which is in the trace).
- **Common causes:** database unreachable (health shows `database: down`); a provider
  returning unusable output (`UpstreamError`, 502, counted in "Failed model calls").
- **Do:** 502s alone are the provider; see *Provider outage*. Anything else: roll back
  if it started with a deploy.

### p95 latency above SLO
- **Look:** "p95 latency by route". If every route is slow together, it's capacity:
  check API CPU. One process saturates a core at ~90 req/s (docs/09).
- **Do:** scale replicas (the HPA targets 65% CPU) or raise `WEB_CONCURRENCY`. Keep
  `replicas × WEB_CONCURRENCY × 15` under Postgres `max_connections`.
- If only `/ask` or assessments are slow: it's the model. See `insightlm.model.duration`.

### Documents stuck at "processing"
- Shouldn't last: permanent failures dead-letter at once, transient ones retry with
  backoff, and the reaper (`requeue_stalled`, every minute) re-enqueues anything
  forgotten. Log line `ingestion_requeued` when it does.
- **If it persists:** is the worker running? (`kubectl get pods -l app.kubernetes.io/component=worker`).
  Is the outbox draining? (`outbox_drained` in worker logs every 10 s when there's work.)
- **Found in practice:** a worker container still running old code after the API was
  redeployed. Worker and API ship together; redeploy both.

### Dead letters rising
- **Look:** "Dead-lettered documents (24h)"; `dead_letters` table has step and error.
- `no text could be extracted`: scanned or image-only PDF. Expected until OCR exists;
  tell the user.
- `could not be opened`: corrupt or encrypted file. Expected; the user is shown why.
- Anything else, in bulk: a code or dependency problem. Fix, then re-drive with the
  reaper by resetting `status='failed', attempts=0` on the affected jobs.

### Provider outage (Gemini / Anthropic)
- **Look:** "Failed model calls": `TransientProviderError` after retries means the
  provider is down or rate-limiting us; `UpstreamError` means it answered with
  something unusable.
- **Behaviour:** calls retry with jitter; then the request fails cleanly with a
  message. Nothing is fabricated: no "absent" verdict is invented for a failed call.
- **Do:** wait, or switch provider (`GENERATION_PROVIDER=anthropic`) if one is
  configured. As a last resort `GENERATION_PROVIDER=offline` keeps the product usable,
  with extractive answers and the baseline judge, labelled as such in the UI.

### A workspace hits its AI budget
- Users see `budget_exceeded` (429). `MODEL_CALLS_PER_DAY` (default 2,000) per
  workspace per UTC day, counted before any call is made.
- **Do:** confirm on "Estimated spend by workspace" that the use is real, then raise
  the limit if it's warranted. It resets at 00:00 UTC.

### Redis down
- **Behaviour, by design:** rate limits and idempotency **fail open** (requests proceed;
  `rate_limiter_unavailable` / `idempotency_store_unavailable` in logs). The model
  budget **fails closed**: AI features return 503 until Redis is back, so nothing is
  spent unmetered. Ingestion stops (the queue is Redis) and resumes on recovery; the
  outbox keeps events safe meanwhile.

### Audit chain reports broken
- `POST /audit/verify` returns the first broken entry. This is a **security incident**:
  the log is append-only for the application role, so a break means privileged access
  or a bug in the chain code.
- **Do:** preserve the database (snapshot) before anything else, then investigate from
  the reported sequence number.

## Data requests

- **Subject access:** owner → the candidate's application page → *Export everything
  held* (or `GET /candidates/{id}/record`).
- **Erasure:** owner → *Erase candidate…* (or `POST /candidates/{id}/erasure`). The
  response says whether verification found residue; if it did, it names table and
  column. Residue is a bug to fix, then erase again.
- **Retention:** Governance → Retention → preview, then erase. Rejected candidates idle
  past `CANDIDATE_RETENTION_DAYS` (default 365).

## Secrets

Environment only (`insightlm-secrets`). Rotating `JWT_SECRET` signs everyone out, which
is the point if it leaked. Provider keys: rotate at the provider, update the Secret,
restart API and worker. Never commit a key; CI and the evaluation gate run keyless.
