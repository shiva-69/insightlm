# 04 — Recruiting Module & Governance Layer

## 1. The shape of the module

Not a CRUD app over a `candidates` table. The domain has real rules, and the rules are where the marks are.

```
Requisition  ──approved──▶  Rubric  ──▶  Assessment  ──▶  Pipeline  ──▶  Decision
   (what we    (human gate)   (evidence,    (stages,        (human,
    need)                      per criterion) SLAs, gates)    logged)
```

### Job requisition → rubric

The JD is prose; the rubric is structure. Turning one into the other is the first useful AI step:

```
Rubric {
  criteria: [{
    label:        "Production experience with distributed payment systems"
    kind:         MUST_HAVE | STRONG_PREFERENCE | NICE_TO_HAVE
    weight:       0..1
    evidence_hint: "look for transaction volume, idempotency, reconciliation, PSP integrations"
    disqualifying: bool
  }]
  approved_by: user_id     ← REQUIRED before any candidate is assessed
  approved_at: timestamp
  version:     int         ← immutable once used in an assessment
}
```

**The human approval gate is a hard constraint, not a UX nicety.** A model must never define the criteria by which people are judged. The system refuses to run assessments against an unapproved rubric, and the test suite asserts that refusal. This single rule is the clearest expression of the project's thesis and it is one line to explain in a viva.

When a rubric is edited after use, it forks to a new version. Existing assessments keep pointing at the version they were run under, and the UI shows "assessed under rubric v2" — so a shortlist is never silently re-scored under criteria nobody applied at the time.

### Candidate intake

- Bulk upload (drag 50 résumés), with per-file progress and per-file failure — one corrupt PDF must not fail the batch.
- **Identity resolution.** The same person applies twice with differently-formatted résumés. Match on normalised email/phone first, then a fuzzy name + employment-history similarity pass, and **propose** merges for human confirmation rather than auto-merging. Keep `identity_link` history so a merge is reversible — because sooner or later you will merge two different people named Rahul Sharma.
- Application = candidate × requisition. A candidate can be in several pipelines with independent state.

### Assessment

Mechanics are in [02-architecture.md §5](02-architecture.md). What matters for the domain:

- Per-criterion, evidence-bounded judgments: `{verdict ∈ strong|adequate|weak|absent, confidence, citations[], rationale}`
- **Aggregation is deterministic code**, not a model output. Weighted sum over verdict values, with `disqualifying` criteria short-circuiting to a flagged state that still requires a human to action.
- `absent` ≠ `weak`. "No evidence found" is a retrieval outcome and must be presented as such — it may mean the candidate lacks the skill, or that the résumé is badly formatted, or that OCR failed. Conflating the two is how good candidates get silently dropped.
- **Confidence-banded output.** Low aggregate confidence routes to mandatory human review rather than producing a confident-looking number.
- The UI shows the **breakdown first, score second**. Deliberate: a score with the evidence one click away invites rubber-stamping; evidence with a score attached invites reading.

### Pipeline & collaboration

- Configurable stages per requisition (Applied → Screened → Interview ×N → Offer → Hired/Rejected), ordered, with SLA timers and a visible ageing view.
- **Stage gates**: cannot advance past Screened without an assessment reviewed by a human; cannot reach Offer without N submitted scorecards. Gates are domain rules with tests, not frontend validation.
- **Rejection requires a reason code** selected by a human. This is both good practice and the thing that makes your adverse-impact reporting possible.
- **Interview kits** generated from the rubric: per-criterion questions, probe follow-ups, and an evaluation guide — so the interview actually tests what the rubric claims to value.
- **Scorecards with anchoring protection**: an interviewer cannot see others' ratings until they submit their own. Small feature, unmistakably the work of someone who has thought about hiring rather than just about software.
- **Interview notes → scorecard draft**: paste or upload notes, get a per-criterion draft with the note excerpts as evidence, which the interviewer edits and signs.
- **Talent-pool semantic search**: natural-language search across all candidate documents in the tenant — *"who has scaled a payments system past 10k TPS?"* — returning candidates with the evidence span that matched. This is the document engine pointed at the whole corpus, and it is the feature that makes the two halves of the project visibly one product.

### Team & access control

Directly answers "he should be able to add members that can help him in recruitment":

| Role | Rubric | Assess | Advance stage | See scores | Scorecard | Invite | Audit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Owner | create/approve | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Recruiter | create, propose | ✓ | ✓ | ✓ | ✓ | – | – |
| Hiring Manager | approve | ✓ | ✓ | ✓ | ✓ | – | – |
| Interviewer | – | – | – | own job only | own only until submit | – | – |
| Coordinator | – | – | scheduling only | – | – | – | – |
| Auditor | read | – | – | ✓ | ✓ | – | ✓ |

Plus **per-requisition scoping** — an interviewer invited for one role sees only that role's candidates. Email invitations with signed, expiring tokens; role changes and revocations audited.

## 2. Governance layer

This is the part that turns "a good project" into "a project that answers a question the industry has not answered well". Build it as a module other modules depend on, not as a feature bolted on in Week 14.

### Audit log

Append-only, hash-chained:
```
audit_entry { id, tenant, actor, action, subject_type, subject_id,
              payload_json, model_version?, prompt_version?,
              prev_hash, entry_hash, at }
```
- Write-only at the application layer; no update or delete path exists, enforced by DB grants as well as code.
- `entry_hash = H(prev_hash || canonical(payload))` — a background verifier walks the chain and alarms on a break. **Write a test that mutates a row and asserts detection.**
- Every AI output, every stage transition, every rubric approval, every access to candidate data, every export.

### Reproducibility

An assessment records `model_version`, `prompt_version`, `retrieval_config_hash`, and the `document_version`s it cited. From that, "Re-run this assessment" is a real button: it produces a fresh assessment and a **diff view** against the original. This supports RQ2/RQ3 directly, and it is a compelling live demo — "here is what this candidate would have scored under last month's pipeline."

### Fairness instrumentation

Handle with methodological care:

- **Blind-screening mode** (see [03 §7](03-ai-engineering.md)) as a per-requisition toggle, on by default for the screening stage.
- **Counterfactual testing** as the primary fairness method: take a résumé, perturb *only* a proxy attribute (name, institution, an address's implied area, a gender-coded activity), re-assess, and measure score drift. Synthetic, controlled, no sensitive data collected. Report drift distributions per attribute class.
- **Selection-rate monitoring** on the synthetic evaluation set, with a four-fifths-rule view, framed as *an instrument the system provides*, not as a compliance certification.
- **Be explicit about limits.** You are not conducting a bias audit; you are building tooling that makes one possible, and validating it on synthetic data. Stating that boundary clearly is more credible than overclaiming, and an examiner will respect it.

### Candidate rights

A candidate-facing view (token-link, no account) showing: that AI assisted the screening, what the rubric criteria were, what evidence was cited about them, who made the decision, and buttons to request correction, human review, or erasure. Each request opens a tracked task with an SLA.

This view takes perhaps three days to build and it is the most memorable thing in the demo, because no other student project will have considered that the person being judged is a user.

### Data lifecycle

- Consent recorded at intake with purpose and expiry.
- Retention policies per document class; scheduled jobs expire and purge.
- Erasure saga with residue verification ([03 §7](03-ai-engineering.md)).
- Export (portability) as a structured bundle.

## 3. The demo narrative

Design the build around this, because it is what actually gets graded:

1. **Upload a corpus, ask a hard question.** Answer streams with citations; click one, the PDF highlights the exact sentence. Ask something the corpus cannot answer — it refuses. *(The engine is real.)*
2. **Create a requisition.** JD → drafted rubric → you edit weights → approve. Try to assess before approving; the system refuses. *(Human authority is structural.)*
3. **Bulk-upload 50 résumés.** Watch the pipeline; one is a scan and goes through OCR; one fails and lands in the DLQ view, visibly. *(Failure is designed for.)*
4. **Ranked shortlist with evidence.** Open a candidate: criterion-by-criterion verdicts, each with a highlighted span. One criterion is `absent` and says so. *(No black-box score.)*
5. **The attack.** Show the injected résumé. Show the baseline being fooled. Show InsightLM flagging it and rendering the hidden text. *(Adversarially aware.)*
6. **Blind mode + counterfactual.** Toggle blind screening; run the name-swap counterfactual; show the drift chart. *(Fairness is instrumented.)*
7. **Invite a teammate.** Second browser, interviewer role — sees only their requisition, cannot see others' scores until they submit. *(Real authorisation.)*
8. **Kill the LLM provider mid-query.** Circuit opens, falls back to the local model, banner explains. Nothing crashes. *(Production thinking.)*
9. **Audit + candidate view.** Show the chain, mutate a row in psql, show the verifier catching it. Open the candidate transparency page. *(Accountability.)*
10. **Grafana + the ablation table.** Traces, token cost per assessment, and the results table with the regression gate in CI. *(Measured, not asserted.)*

Ten minutes, and every claim in the dissertation has been shown working.
