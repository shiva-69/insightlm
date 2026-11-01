# 03 — AI Engineering

This is the technical heart of the dissertation. The engineering goal is not "make the RAG work" — it is **make the RAG measurable, then make the measurements go up, and show the work.**

## 1. The citation contract

One invariant the whole system is built around:

> Every AI-generated claim is a tuple `(text, citation_ids[])` where each citation resolves to a persisted `chunk_span` with page number, bounding box and character offsets. A claim that fails resolution is not shown to the user.

Implementation:
1. **Structured output.** The model is constrained to a schema (`{claims: [{text, citation_ids}]}`), not free prose. Use native structured-output/tool-calling, not regex over markdown.
2. **Stable IDs in context.** Chunks enter the prompt tagged `[C7]`, and the model cites those tags. Never ask it to reproduce a source name or page — it will get it subtly wrong.
3. **Post-generation validation.** A validator checks every cited ID exists, is in the retrieved set, and (optionally) that the claim is entailed by its cited span via an NLI check or a cheap judge model.
4. **On failure:** one retry with the failure fed back, then degrade to showing retrieved evidence without synthesis. Never silently drop the citation requirement.
5. **Refusal is a first-class output.** "The corpus does not contain evidence for this" is a success, and it is measured (see §5, answer-relevance vs. abstention).

The UI half matters as much: clicking a citation must scroll and highlight the span in the actual PDF. This is the three-second impression that makes the project land.

## 2. Ingestion

**Parsing** — naive `extract_text()` is where most student RAG quietly dies. Résumés are two-column, table-laden, and often scanned.
- Native PDFs: PyMuPDF for text + geometry (you need the bounding boxes for highlighting).
- Layout understanding: a document-layout model to segment columns, headers, tables — so a two-column résumé does not interleave into nonsense.
- Tables: extract to structured form, serialise as markdown into the chunk (tables embedded as flat text retrieve terribly).
- Scanned/image PDFs: detect low text-density, fall back to OCR (PaddleOCR or Tesseract). **Record OCR confidence** and surface low-confidence documents — an unreadable résumé silently scoring zero is exactly the kind of failure that makes a system unfair.
  - *As built:* a page with an image and under 60 visible characters is read by Tesseract through PyMuPDF at 300 dpi. Character boxes come back like a born-digital PDF's, so the citation contract holds. Any existing text layer is ignored for that page (its invisible text is reported as concealed), and the document is labelled "Scanned · OCR" wherever a reviewer relies on it. Per-word confidence is not recorded yet; the label is the surfacing. Without Tesseract installed, a scan is flagged as unread rather than ingested as silently empty.
- DOCX, TXT, MD, and (for the document module) PPTX/HTML.

**Sanitisation** — runs before anything else touches the text. See §6.

**Chunking** — structure-aware, not fixed-width:
- Split on layout structure (sections, headings) first; only then by token budget with overlap.
- Never split a table or a list item mid-row.
- Each chunk carries `{section_path, page, bbox, char_range, doc_version}`.
- **Contextual enrichment:** prepend a short generated summary of the parent section to each chunk before embedding. This measurably lifts retrieval on chunks that are meaningless standalone ("Reduced latency by 40%" — of *what*?). Cheap to do at ingest, and it is a clean ablation arm.

**Résumé schema extraction** — the specialisation that makes the recruiting module work. A second pass produces a typed profile:
```
ExtractedProfile {
  experiences: [{ company, title, start, end, is_current, summary, evidence_span }]
  skills:      [{ name, first_seen_year, evidence_spans[] }]
  education:   [{ institution, degree, field, year, evidence_span }]
  total_experience_months: <computed from date ranges, NOT asked of the LLM>
}
```
Two rules: **every extracted field carries its evidence span**, and **anything arithmetic is computed in code**. Asking a language model to add up tenure is asking for a wrong number with no audit trail. Overlapping date ranges, "present", and gaps are handled by a tested interval-merge function — a small, unglamorous piece of correctness that reviewers notice.

## 3. Retrieval

```
query
 ├─ query planning
 │    ├─ rewrite (resolve pronouns/context from conversation)
 │    ├─ decompose (multi-hop → sub-questions)
 │    └─ HyDE (embed a hypothetical answer, not the question)
 ├─ parallel retrieval                      [asyncio.gather]
 │    ├─ lexical:  Postgres tsvector, ts_rank_cd     → top 50
 │    └─ dense:    pgvector HNSW, cosine             → top 50
 ├─ Reciprocal Rank Fusion  (k=60)                   → top 50
 ├─ cross-encoder rerank (bge-reranker-v2-m3)        → top 8
 └─ context assembly (dedup, budget, stable IDs)
```

Why each stage earns its place — and each is an **ablation arm**:
- **Lexical** catches exact terms dense retrieval fumbles: `Kubernetes`, `PCI-DSS`, `CKA`, a specific framework version. In a hiring corpus this is not a marginal case, it is the common case.
- **Dense** catches paraphrase: "built the checkout flow" ↔ "payment integration experience".
- **RRF** fuses without needing calibrated scores across two incomparable scales.
- **Reranking** is usually the single largest quality jump per unit of effort. Retrieve broadly, rank precisely.
- **HyDE/decomposition** help on comparative and multi-hop questions and hurt on simple lookups — which is itself a finding worth reporting.

**Retrieval must be tenant-filtered inside the SQL**, not after. Post-filtering an ANN result set silently shrinks recall and, worse, is one bug away from a cross-tenant leak.

## 4. Generation

- **Model tiering.** A cheap/fast model for extraction, classification and query rewriting; a strong model for judgment and synthesis. Route by task, record the routing, and make it a config knob so RQ3's cost/accuracy curve is a config sweep rather than a rewrite.
- **Prompts are versioned assets.** Stored as content-hashed `prompt_version` rows, referenced by ID in every output. Editing a prompt creates a new version; it never mutates history. This is what makes "reproduce this assessment" a real operation.
- **Streaming** via SSE with citations resolved progressively.
- **Semantic cache.** Normalise → embed → similarity lookup in Redis; hit above threshold within the same tenant and corpus version. Publish the hit rate and the cost saved — it is a clean quantitative result.
- **Fallback chain:** primary hosted model → secondary → local model → retrieval-only with an honest banner. Never a blank screen.

## 5. Evaluation harness — build this in Week 3, before tuning anything

This is the difference between a project and a dissertation. If you tune the pipeline before you can measure it, you have no results chapter and no defence for any choice you made.

**Golden dataset** — ~150–200 items over a synthetic corpus you generate and control:
- Document QA: question, ground-truth answer, ground-truth relevant chunk IDs
- Multi-hop and comparative questions
- **Unanswerable questions** (the correct output is refusal) — typically 15–20% of the set
- Résumé→criterion judgments with human-labelled verdicts and expected evidence
- Adversarial items (see §6)

Build it semi-automatically: generate candidate Q/A from chunks with a strong model, then **hand-review every item**. Say so in the methodology — reviewer-verified synthetic data is a legitimate, standard approach, and being explicit about it is a strength.

**Metrics**
| Layer | Metrics |
| --- | --- |
| Retrieval | Recall@k, Precision@k, MRR, nDCG@10 |
| Generation | Faithfulness / groundedness, answer relevance, citation precision & recall, hallucination rate |
| Abstention | Correct-refusal rate on unanswerable items, over-refusal rate on answerable ones |
| Extraction | Field-level precision/recall/F1 vs. labelled profiles |
| Assessment | Agreement with human labels (Cohen's κ), rank correlation (Spearman ρ) against a human-ranked shortlist |
| Robustness | Injection block rate, redaction leakage rate |
| Ops | p50/p95 latency per stage, tokens and cost per request, cache hit rate |

**Ablation matrix** — the core results table:

| Config | Lexical | Dense | RRF | Rerank | HyDE | Ctx. enrich | Recall@10 | nDCG | Faithful. | p95 | $/query |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| A — direct LLM, no retrieval | – | – | – | – | – | – | | | | | |
| B — naive dense top-k | | ✓ | | | | | | | | | |
| C — hybrid + RRF | ✓ | ✓ | ✓ | | | | | | | | |
| D — + rerank | ✓ | ✓ | ✓ | ✓ | | | | | | | |
| E — + query transform | ✓ | ✓ | ✓ | ✓ | ✓ | | | | | | |
| **F — full** | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | | | | | |

Run each config ≥3 times, report mean and spread — LLM evaluation is noisy and reporting a single run is a methodological hole an examiner may probe. Note where cost/latency makes a higher-quality config the wrong choice; that discussion is worth more marks than the best number in the table.

**CI regression gate:** a reduced golden set runs on every PR touching the pipeline. Faithfulness or Recall@10 dropping more than 3% below the recorded baseline fails the build. Being able to say *"you cannot merge a change that makes retrieval worse"* is a strong claim very few student projects can make.

**LLM-as-judge caveat:** where a judge model scores outputs, validate it against your human labels on a subset and report the agreement. Do not present judge scores as ground truth without that check.

## 6. Adversarial robustness — prompt injection in résumés

A real, documented attack: candidates embed instructions in their PDF that the ATS's language model reads but a human never sees. Vectors include white-on-white text, 1pt fonts, text positioned off-page, content hidden behind images, and PDF metadata fields.

This is an excellent feature to build and an even better one to demo — you can show the attack working against a naive baseline and being caught by your pipeline, live.

**Layered defence:**
1. **Detection at parse.** Compare text colour against background; flag sub-threshold font sizes; flag glyphs outside the page media box; scan XMP/Info metadata. Emit a `suspicious_content` finding with the offending span.
2. **Instruction/data separation.** Document text is delimited and labelled as untrusted data in the prompt, with a standing rule that content inside it is never an instruction. Necessary, not sufficient — say so.
3. **Structural constraint.** Schema-constrained output limits the blast radius: a model that can only emit `{verdict, confidence, citations}` cannot be talked into emitting a recommendation.
4. **Evidence validation.** A claim must cite a real span, so a fabricated qualification with no supporting text fails validation.
5. **Narrow questions.** Per-criterion judgment means injected text rarely sits in the retrieved window for the criterion it is trying to influence.
6. **Human review.** Flagged documents surface a banner; the recruiter sees the hidden text rendered visibly.

**Red-team corpus:** ~40 adversarial résumés spanning each vector, plus benign controls to measure false positives. Report block rate *and* false-positive rate — a detector that flags everything is not a defence, and acknowledging that trade-off is the mark of someone who has shipped a detector before.

## 7. PII, redaction, and erasure

- **Detection at ingest:** phone, email, address, national IDs, DOB, URLs — regex plus an NER pass. Stored as typed spans, not deleted.
- **Blind-screening mode:** a redaction profile masks name, gender markers, photo, age/graduation-year proxies, institution name, and location *at context-assembly time*, so the same stored document serves both blind and identified views. Redaction at read time, not write time — otherwise you cannot un-blind for the interview stage.
- **Erasure is the hard one.** A deletion request must purge the source object, every `document_version`, every chunk, every embedding, derived profiles, assessments, cached answers, *and* semantic-cache entries — while the audit log retains a tombstone proving the erasure happened without retaining the data. Implement it as a saga with a verification step, and **write an automated test that asserts no residue remains in any store.** Very few systems get this right; having a passing test for it is a genuinely strong claim.
