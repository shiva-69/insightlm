# 05 — Delivery Plan

**Assumed window:** 16 build weeks, **Mon 21 Sep 2026 → Sun 17 Jan 2027**, at ~15–20 hrs/week solo, followed by 2 weeks (18–31 Jan 2027) of dissertation consolidation and viva prep. Adjust to your actual submission date; §5 covers compressing and extending.

## Sequencing principles

1. **Thin vertical slice first.** By end of Week 4 a document must be uploadable, queryable, and answerable *with a working citation highlight*. Every later week deepens a path that already runs end to end. Horizontal layer-by-layer building is how projects arrive at Week 14 with no demo.
2. **Evaluation before optimisation.** The harness (Week 5) predates all tuning. Otherwise there is no results chapter and no evidence for any decision.
3. **Governance is a foundation, not a finish.** The audit log and version registry land in Week 2, because retrofitting provenance into an existing codebase is miserable and always partial.
4. **One de-risking spike per phase.** Hit the unknown early, in a throwaway branch, before it is on the critical path.
5. **Write the dissertation continuously.** Each phase ends with its chapter section drafted while the details are fresh. The ADRs *are* your design chapter.

---

## Phase 0 — Foundation · Weeks 1–2 (21 Sep – 4 Oct)

**Goal:** a repo a senior engineer would recognise, and a boring thing deployed end to end.

- Monorepo: `src/insightlm/` (modules + `api/` + `worker/`), `web/` (Next.js), `migrations/`, `eval/`, `infra/`, `docs/`
- `uv` project setup; `docker compose up` brings up Postgres+pgvector, Redis, MinIO, OTel collector, Grafana, api, worker and web
- Alembic migrations; `pydantic-settings` config; `structlog` JSON logging with a correlation-ID context var
- **The skeleton of every module first** — `domain/ ports/ application/ adapters/` created empty, so nothing is ever written in the wrong place
- **`.importlinter` written before the second module exists**, with all three contracts; commit a deliberate violation and screenshot CI rejecting it
- `mypy --strict` wired in now, not later — retrofitting strict typing is miserable
- The **unit-of-work adapter** and `bootstrap.py` composition root; the one-`commit()` rule established
- JWT auth + argon2; tenant/user/membership/invitation; `PolicyPort`; **Postgres RLS policies** with a test that disables the app-layer check and confirms RLS still blocks
- **Audit log with hash chaining, from day one** — every write path uses it
- Prompt/model version registry tables; transactional outbox + relay
- GitHub Actions: ruff, mypy, import-linter, pytest, trivy, pip-audit, gitleaks, migration-drift gate, image build
- ADR-0001 (single Python service, hexagonal), ADR-0002 (Postgres+pgvector as single store), ADR-0003 (in-app auth over Keycloak), ADR-0005 (unit of work), ADR-0014 (ARQ + outbox), ADR-0016 (RLS for tenancy)
- **Spike:** PyMuPDF geometry extraction → can you round-trip a char range to a bounding box and back? Everything downstream depends on this.

**Exit:** login, create tenant, invite a user, see the audit chain in the DB, CI green (including a passing import-linter), one trace visible in Grafana.

> Phase 0 is heavier than it looks, and deliberately so. `import-linter`, `mypy --strict`, RLS and the unit-of-work are all things that are nearly free now and nearly impossible to retrofit in Week 10. This is the week that decides whether the codebase reads as senior.

---

## Phase 1 — Document engine, vertical slice · Weeks 3–4 (5 – 18 Oct)

**Goal:** upload → ask → cited answer → click → highlighted span.

- Upload to MinIO with checksum dedup; `document` / `document_version`
- Outbox → relay → ARQ → ingestion worker with typed, resumable `ingestion_step`s; dead-letter table
- Parse (PyMuPDF + layout), chunk (structure-aware, with spans), embed, index into pgvector **and** tsvector
- Dense retrieval + a first generation pass with structured citation output
- Citation validator (reject unresolvable claims)
- Evidence viewer: upload, document list, ask, **PDF.js viewer with span highlighting**
  (delivered as a single page served by the API; the Next.js app is deferred to Phase 5
  where collaboration UI makes it earn its keep)
- ADR-0004 (chunking strategy), ADR-0006 (citation contract)

**Exit:** the money demo works. Milestone **M1**. ✅ *Complete (18 Sep 2026).*

> If Week 4 ends without a working highlight, stop and fix it before proceeding. It is the project's spine.

---

## Phase 2 — Evaluation harness · Week 5 (19 – 25 Oct)

**Goal:** measurement before tuning.

- Synthetic corpus generator (documents + ~200 synthetic résumés across seniority, formats, quality — include badly-formatted and scanned)
- Golden dataset: QA pairs, multi-hop, unanswerable items, labelled relevant chunks. Generate, then **hand-review every item**
- Metric implementations: Recall@k, MRR, nDCG, faithfulness, citation precision/recall, abstention rates
- `eval_run` persistence with full config snapshot; CLI runner; HTML report
- Baseline recorded for config **B (naive dense)** and **A (direct LLM, no retrieval)**
- CI regression gate wired on a reduced set

**Exit:** `make eval` prints a table you trust. Milestone **M2**. ✅ *Complete (18 Sep 2026) — see [docs/07](07-evaluation-findings.md).*

---

## Phase 3 — Retrieval quality · Weeks 6–7 (26 Oct – 8 Nov)

**Goal:** move the numbers, and record why each stage earned its place.

- Lexical retrieval (tsvector, `ts_rank_cd`) + RRF fusion → measure
- Cross-encoder reranker behind a `RerankPort` (local ONNX in the worker; hosted API as the alternate adapter) → measure
- Query transformation: rewrite, decompose, HyDE → measure (expect mixed results; report honestly)
- Contextual chunk enrichment → measure
- Semantic cache in Redis; token/cost accounting per call
- `tenacity` retry-with-jitter + circuit breaker + bounded-semaphore bulkhead; local-model fallback
- Run the **full ablation matrix**, ≥3 runs per config
- ADR-0007 (hybrid + RRF), ADR-0008 (rerank model choice), ADR-0009 (caching)

**Exit:** the results table is populated and defensible. Milestone **M3** — *the dissertation's core contribution now exists.*

---

## Phase 4 — Recruiting domain · Weeks 8–10 (9 – 29 Nov)

**Goal:** a real ATS on top of the engine.

- **W8:** Requisitions; JD → rubric drafting; rubric editor; **approval gate** (+ the test that proves assessment is refused without it); rubric versioning/forking
- **W9:** Bulk résumé intake with per-file status; typed profile extraction with evidence spans; date-interval arithmetic in code (unit-tested); identity resolution with human-confirmed merges
- **W10:** Per-criterion assessment fan-out via `asyncio.gather` under a bounded semaphore, **holding no DB session**; deterministic aggregation; `absent` vs `weak` handling; confidence banding; evidence-first candidate detail UI; ranked shortlist
- Assessment reproducibility: re-run + diff view
- ADR-0010 (per-criterion isolation over whole-résumé scoring), ADR-0011 (deterministic aggregation)

**Exit:** 50 résumés in, evidence-linked ranked shortlist out. Milestone **M4**. ◐ *Core complete (18 Sep 2026).*

> **Delivered:** requisitions; versioned rubrics with the approval gate and the test
> that proves refusal; separation of duties on approval (owner excepted, see ADR-0011);
> per-criterion assessment scoped to the candidate's own documents, with judging fanned
> out under a semaphore and no DB session held; deterministic aggregation; `absent` vs
> `weak`; confidence flag; evidence-first assessment view with in-document highlights;
> ranked shortlist; ADR-0010, ADR-0011.
>
> **Since delivered (19 Sep):** bulk intake with per-file status, and the exit check
> itself: 50 synthetic CVs of known strength through the full pipeline, shortlist
> Spearman ρ = 0.86 against the truth with the offline judge, and the must-have flag
> correct for all 50.
>
> **Carried forward, not dropped:** JD → rubric drafting (a model *proposing* criteria
> that a human still approves); bulk intake with per-file status; typed profile
> extraction and date arithmetic; identity resolution; the re-run diff view. None of
> these changes the domain rules above. They're additional inputs to them. The
> 50-résumé exit check waits for bulk intake.

---

## Phase 5 — Collaboration & pipeline · Week 11 (30 Nov – 6 Dec)

- Pipeline stages, transitions, gates, SLA timers, ageing view
- Invitations, role assignment, per-requisition scoping; cross-tenant access test on every endpoint
- Scorecards with anchoring protection; interview kit generation; notes → scorecard draft
- Talent-pool semantic search
- Reason codes on rejection

**Exit:** two browsers, two roles, correct and different views. Milestone **M5**. ◐ *Core complete (18 Sep 2026).*

> **Delivered:** stages with forward-only transitions and two domain gates (screening
> needs an assessment; an offer needs two submitted scorecards); reason codes on every
> rejection; append-only stage history the app role can't update; per-stage SLAs and an
> overdue marker; interview panels; scorecards with anchoring protection (withheld until
> you submit, drafts never shown, submissions final); per-requisition scoping for
> interviewers, returning 404 rather than 403; score-free interviewer views; talent-pool
> search returning people with their matching passages. Exit check passes: an
> integration test and a browser walkthrough with an owner and an interviewer.
>
> **Since delivered (19 Sep):** the talent-search cutoff, measured (Finding 20).
>
> **Carried forward:** interview-kit generation and notes → scorecard draft (both need a
> model; they wait for a deliberate paid run); configurable per-requisition stages and
> scorecard thresholds; a relevance cutoff for talent search, which today returns every
> candidate ranked, however weak the match. That cutoff should be set from measurements,
> not guessed.

---

## Phase 6 — Governance, safety, fairness · Weeks 12–13 (7 – 20 Dec)

**Goal:** the differentiator.

- **W12:** Prompt-injection detection (hidden text, micro-fonts, off-page, metadata); red-team corpus of ~40 adversarial + benign controls; measure block rate *and* false-positive rate; reviewer-facing "hidden text found" rendering; baseline-vs-defended comparison for the demo
- **W13:** PII detection; redaction profiles; blind-screening mode at context-assembly time; counterfactual fairness harness (name/institution/location perturbation → score drift); selection-rate + four-fifths view; erasure saga with **automated residue verification**; candidate transparency view; consent and retention
- ADR-0012 (injection defence layers), ADR-0013 (read-time redaction), ADR-0015 (erasure saga)

**Exit:** the attack demo works both ways; the erasure test passes. Milestone **M6**. ✅ *Complete (19 Sep 2026).*

> **Delivered:** the layered injection defence measured on `redteam-v1` (ADR-0012);
> read-time blind screening (ADR-0013); a counterfactual fairness harness with repeated
> samples and a permutation test; voluntary self-identification with a four-fifths
> selection-rate report; verified erasure with a negative-control test (ADR-0015); a
> subject-access record; retention through the same erasure. Findings 14–18 in docs/07.
>
> **Not done:** PII *detection* in free text beyond the known name (we redact what we
> know, not what we'd have to guess); consent capture from the candidate directly (the
> owner records self-identification on their behalf). (OCR for scanned CVs followed in
> the hardening work; see docs/08.)

---

## Phase 7 — Hardening & production posture · Week 14 (21 – 27 Dec)

- Full OTel trace continuity api → worker → LLM; Grafana dashboards (latency per stage, tokens, cost/tenant, cache hit rate, dead-letter depth)
- k6 load test at 50 concurrent users; publish results against the SLOs
- STRIDE threat model → verify each control exists; fix what does not
- Rate limiting, per-tenant budget caps, idempotency keys on all mutating endpoints
- Test coverage pass on domain + pipeline; testcontainers-python integration suite; `schemathesis` fuzz run against the generated OpenAPI
- Runbook, SLOs, Helm/K8s manifests (as artifact), seed/demo data script
- Accessibility and responsive pass on the web app

**Exit:** Milestone **M7**. ✅ *Complete (19 Sep 2026).*

> **Delivered, each with its evidence:**
> - **One trace from upload to model call**, across the outbox and queue; Tempo,
>   Prometheus and a provisioned Grafana dashboard (latency by route, model calls,
>   tokens and estimated spend per workspace, ingestion steps, dead letters).
> - **Load test against declared SLOs** (docs/09): 50 users, 0 failures, p95 58 ms. At
>   200 users one API process pinned a core; four processes took p95 from 1.73 s to
>   115 ms.
> - **STRIDE threat model** (docs/08), every control pointing at its test.
> - **Rate limits, a per-workspace model budget, idempotency keys**; a deliberate Redis
>   outage policy (limits fail open, spend fails closed).
> - **schemathesis**: 2 server errors and 8 schema violations found and fixed; 3,183
>   generated requests pass. **Coverage** 89% (domain 96%), measured correctly once
>   greenlets were tracked, with an 85% floor in CI. 393 tests.
> - **Runbook** (docs/10), SLOs, **Kubernetes manifests** validated by kubeconform, a
>   **seed script**, and a reaper for ingestion nothing else would retry.
> - **Accessibility**: 0 axe violations (WCAG 2.1 AA) on 13 pages after fixing text
>   contrast; no overflow at 390 px.
>
> **Not done:** Helm packaging (plain manifests with kustomize instead); httpOnly-cookie
> sessions and a CSP (docs/08, known gaps); the PDF viewer is desktop-only, though the
> cited quotes show on phones.
>
> *Later:* the hardening work closed the last three: cookie sessions, a CSP and two-factor
> sign-in (ADR-0023), and a document viewer that opens as a full-screen sheet on phones,
> with zoom.

---

## Phase 8 — Polish, dissertation, rehearsal · Weeks 15–16 (28 Dec – 17 Jan)

- **W15:** UI polish; empty/loading/error states everywhere; final ablation re-run on frozen code; all figures and tables generated; C4 diagrams finalised; ADRs completed (target ~18)
- **W16:** **Code freeze Wed 14 Jan.** Dissertation assembly; 10-min demo rehearsed to script ×5, including the deliberate failure demos; a recorded backup video (never demo live without one); viva Q&A prep — especially *"why not microservices"*, *"why not a vector DB"*, *"how do you know it works"*, *"what would you do differently"*
- Final: README with one-command setup, architecture diagram, demo GIF

**Exit:** Milestone **M8** — submission ready. ◐ *In progress (19 Sep 2026).*

> **Done:** all 18 ADRs; C4 context, container and component diagrams and a generated ER
> diagram (docs/11, `make diagrams`); the final evaluation re-run on frozen code into
> `eval/results/`, which surfaced a nondeterministic ranking (fixed, Finding 22) and a
> fairness claim that didn't replicate (retracted, Finding 21); figures built from
> results (`make figures`); error states on every data view; the README rewritten to
> match what exists.
>
> **Left, and mostly the author's:** the dissertation text, the recorded demo video and
> rehearsal, and viva preparation. A demo script to rehearse against is the next item.

---

## Risk register

| # | Risk | L | I | Mitigation | Trigger |
| --- | --- | --- | --- | --- | --- |
| R1 | Scope overrun — too many features, none finished | **H** | **H** | Phases 0–4 are the graded core; 5–6 are ranked and individually droppable; 7–8 are fixed and non-negotiable | Any phase slipping >4 days |
| R2 | Citation highlighting proves harder than expected | M | **H** | Spiked in Week 1; fallback is page-level highlight + text snippet rather than exact bbox | Spike unresolved by end of W1 |
| R3 | LLM cost overrun | M | M | Cheap models for extraction; aggressive semantic caching; eval on a reduced set by default; hard budget cap with alerting | 50% of budget before W8 |
| R4 | Provider outage / key issue during the demo | M | **H** | Local model fallback built in W7; recorded demo video; seeded demo tenant with cached responses | — |
| R5 | Eval metrics too noisy to support conclusions | M | M | ≥3 runs per config with reported spread; fixed seeds; deterministic settings where possible; validate judge model against human labels | Spread exceeding between-config gaps |
| R6 | Résumé parsing quality worse than hoped | M | M | OCR fallback + confidence surfacing; treat parse quality as a reported metric, not a hidden assumption | Extraction F1 < 0.7 at W9 |
| R7 | Solo + full-time job → sustained velocity drop | **H** | **H** | Weekly milestone check; Week 17–18 buffer; §5 compression plan pre-agreed with your guide | Two consecutive missed weekly exits |
| R8 | Over-claiming fairness/compliance in the writeup | M | **H** | Explicit limitations section; "instrumentation, not certification" framing throughout | — |
| R9 | Synthetic data judged insufficient by examiner | L | M | Document the generation + human-review methodology in the dissertation; cite it as a deliberate ethics decision | — |

**Weekly ritual (30 min, Sundays):** did I hit the exit criterion; what is the single biggest risk to the next milestone; what gets cut if this week slips. Keep it in a `docs/journal.md` — it becomes your project-management chapter for free.

---

## 5. Flexing the plan

**If you have only 12 weeks** — keep Phases 0–4 and 7–8 intact. Then:
- Cut interview kits, notes→scorecard, talent-pool search (keep scorecards + pipeline)
- Cut counterfactual fairness harness; keep blind mode and erasure
- Reduce ablation to configs A, B, D, F
- **Never cut:** the citation contract, the evaluation harness, the approval gate, the audit chain, the injection defence. Those are the thesis.

**If you have 24 weeks** — add, in this order of value:
1. A second vertical on the same engine (e.g. contract/policy review) — this *proves* the horizontal-engine claim rather than asserting it. Highest-value addition by far.
2. A small human study: 3–5 recruiters compare InsightLM shortlists against manual review; report agreement and time-to-shortlist. Turns RQ1 from a machine-measured claim into an evaluated one.
3. Agentic multi-hop research over the corpus, with a plan-execute-verify loop and its own eval set.
4. Fine-tuned small model for extraction, benchmarked against the prompted baseline on cost/latency/F1.

**Cut list, in the order you should cut** (decide this now, not at 2am in Week 13): talent-pool search → interview kits → notes→scorecard → identity resolution (manual merge only) → SLA timers → K8s manifests → counterfactual harness → HyDE arm of the ablation.

---

## 6. Deliverables checklist

**System:** monorepo, `docker compose up` working from clean clone, seed script with demo tenant + corpus, recorded demo video.

**Documents:** dissertation; ~18 ADRs; C4 diagrams (context, container, component); ER diagram; threat model; SLO + runbook; API reference (OpenAPI, generated); evaluation report with the ablation table; limitations & ethics statement.

**Evidence of engineering rigour:** CI history showing the regression gate catching a real regression; the commit where `import-linter` rejected a boundary violation; load test results; testcontainers integration suite; the `journal.md`.
