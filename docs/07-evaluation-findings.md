# 07 — Evaluation: harness and first findings

Milestone **M2**. The harness exists, it runs against the real pipeline, and it has
already produced findings the test suite could not.

## How a run works

1. Ingest the synthetic corpus into a throwaway tenant **through the real pipeline** —
   upload, parse, chunk, embed, index. Evaluating against a hand-built index would
   measure a system nobody runs.
2. Resolve each golden item's quotes against the chunks *that configuration produced*.
3. Ask every question through the real ask path and score the result.

```bash
make eval                     # every configuration
make eval-gate                # the CI gate, offline and deterministic
uv run insightlm-eval --config B-dense --out run.json
```

## Labels are quotes, not chunk ids

The decision the harness rests on. A chunk id is assigned at ingestion and changes
whenever the chunking strategy changes — and chunking strategy is an **ablation arm**.
Labelling by chunk id would mean the ground truth silently invalidates itself the
moment you vary the thing you are measuring, and every configuration would be scored
against a different truth.

So an item records the *text* that must be retrieved, resolved at run time by
substring match on normalised whitespace. Labels survive re-chunking, re-embedding and
re-ingestion. `tests/unit/test_golden_dataset.py` pins the property directly: the same
quote resolves whether the corpus was split into three chunks or one.

Unmatched quotes are reported, never dropped. A quote matching nothing makes an item
look unanswerable and deflates recall with no error anywhere.

## The seed set

14 items over 5 synthetic documents: 6 factual, 2 multi-hop, 2 comparative, 4
unanswerable (29%). Hand-written, each recording *why* it exists — an item whose
purpose nobody can state is one that gets silently broken.

Synthetic by choice. Real résumés are personal data, and a hiring project that
evaluates on scraped CVs has undermined its own argument before it starts. Generating
the corpus also means ground truth is *known* rather than inferred. The corpus is
byte-deterministic, so runs are reproducible and re-generating it deduplicates instead
of re-ingesting.

It includes the awkward cases on purpose: a two-column CV that naive extraction
interleaves, and a document with no extractable text that ingestion must fail
*visibly*. It does, every run.

## First results — `seed-v1`, 18 Sep 2026

| Config | R@8 | nDCG | MRR | MAP | cite-P | correct refusal | over-refusal | p50 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| B — dense (gemini-embedding-001 + gemini-2.5-pro) | 1.000 | **0.969** | 0.950 | 0.958 | 0.818 | 0.75 | 0.00 | 8.1 s |
| B — dense (offline: hashing + extractive) | 1.000 | **0.699** | 0.608 | 0.581 | 0.442 | 0.50 | 0.20 | 1 ms |
| L — lexical only (control) | 0.100 | 0.100 | 0.100 | 0.100 | 1.000 | 1.00 | 0.90 | 1 ms |

Citation contract held in every run: **0 unresolvable citations, 0 uncited claims.**

### Recall is saturated, and saying so matters

R@8 is 1.000 for both dense configurations. That is not a result — it is an artefact
of a five-document corpus with `k=8`, where retrieval returns nearly everything. A
metric that cannot go down cannot detect a regression, which is why the CI gate is on
**nDCG**: it is sensitive to *ordering*, so it still moves when quality does.

Recall becomes the headline again once the corpus reaches the ~200 documents docs/05
plans for. Reporting 1.000 as though it were an achievement would be the easiest way
to make this project look stronger than it is.

### Finding 1 — negated near-misses are answered, not refused

`q-meera-settlement` asks what settlement systems Meera Iyer has built. Her CV says:

> "Designed the reconciliation reports used by the finance team, **though not the
> settlement system itself**."

Retrieval matches on *settlement* and *reconciliation*. The generator read the passage
and answered anyway. Correct-abstention is 0.75 because of this one item.

This is the most useful thing the harness has produced. It is a specific, reproducible
failure with a named cause — the model does not weight an explicit negation inside a
retrieved passage — and it is exactly the class of error that matters in hiring, where
a near-miss reads as a match. Candidate fixes for Phase 3: an entailment check between
claim and cited span, or a prompt instruction addressing negation directly. Whichever
is tried, this item measures it.

### Finding 2 — comparative questions shotgun citations

| Item kind | citations per answer | cite-P |
| --- | --- | --- |
| factual | 1 | 1.00 |
| multi-hop | 1–2 | 1.00 |
| comparative | 5–6 | 0.33–0.40 |

On comparative questions the model cites nearly everything it read rather than the
passages supporting each specific claim. Every citation still *resolves* — the
contract holds — but precision collapses. A reader clicking through finds passages
that do not support the sentence they came from, which erodes exactly the trust the
citation was meant to create.

Citation precision is the metric that catches this, and it is why it is reported
separately from the contract check.

### Finding 3 — the lexical control, read correctly

Lexical-only scores 0.100 and over-refuses on 90% of answerable items. The tempting
reading is "lexical retrieval is useless". The honest one is that
`websearch_to_tsquery` applies AND semantics over stemmed terms, so a conversational
question — *"What transaction throughput did the settlement platform sustain?"* —
matches nothing, and the system correctly declines rather than inventing. The arm is
misconfigured for this query distribution, not worthless.

Its value is exact-term recall that dense retrieval fumbles: `PCI-DSS`, `CKA`, a
framework version. That is a complement, not a substitute, which is why Phase 3 fuses
the two with RRF rather than choosing between them. This run is the evidence for that
design rather than an assumption behind it.

### The metric design, validated by its own output

Lexical-only scores **1.00 on correct abstention** — a perfect score — while
abstaining on 90% of answerable questions. Had the two rates been collapsed into a
single "abstention accuracy", this configuration would have looked excellent while
answering almost nothing. Reporting them separately is what makes the number
interpretable.

## The regression gate

CI runs `--config B-dense --min-ndcg 0.67` against the **offline** adapters.

Deliberately keyless: the offline adapters are deterministic, so a drop is a real
pipeline regression rather than model variance, and a PR gate that depends on a
third-party API fails for reasons the PR did not cause. Provider-dependent numbers
belong in a keyed nightly run.

The threshold comes from `eval/baselines.json` — a measured run, 3% below the recorded
offline nDCG of 0.699, per docs/06 §4. Both directions are verified: the gate passes
at 0.67 and fails at 0.95.

A violated citation contract fails the gate regardless of any threshold. It is an
invariant, not a quality target.

---

# Phase 3 — the ablation

Corpus grown to 40 generated documents; 30 golden items derived from the facts they
were rendered from. Gemini for both halves. One run per arm.

| Config | R@8 | nDCG | MRR | cite-P | correct refusal | over-refusal | p50 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| B — dense | 0.773 | 0.469 | 0.373 | 0.944 | 0.875 | 0.227 | 7.8 s |
| **C — dense + context** | **0.955** | **0.965** | **1.000** | 0.846 | 0.500 | 0.000 | 8.8 s |
| D — hybrid RRF | 0.955 | 0.965 | 1.000 | 0.846 | 0.500 | 0.000 | 7.6 s |
| L — lexical only | 0.000 | 0.000 | 0.000 | 0.000 | 0.500 | 1.000 | 1 ms |

Citation contract held in every arm: 0 violations.

## Finding 4 — contextual enrichment is the whole story

| Item kind | B (no context) | C (with context) |
| --- | --- | --- |
| factual nDCG | 0.501 | **1.000** |
| multi-hop R@8 | 0.167 | **0.833** |
| multi-hop nDCG | 0.053 | **0.871** |
| multi-hop abstentions | 5 of 6 | 0 of 6 |

nDCG roughly doubles and MRR goes to 1.000 — the right passage is now ranked first for
every answerable item. Multi-hop goes from near-total failure to working.

The diagnosis held. A passage describing an achievement never named the person who
achieved it, because the name sat in the header chunk. Prepending the document title
before embedding — while leaving the *cited* span as the passage itself — fixes the
class of failure rather than the symptom. This is the clearest result in the project
so far, and it came from a measurement rather than from intuition.

## Finding 5 — hybrid RRF earns nothing here, and I can say why

C and D are **identical on every retrieval metric**. Fusion added exactly zero.

Not a bug: the lexical arm returns nothing (row L is 0.000 across the board), so RRF
fuses dense against an empty list and returns dense. Fusion cannot contribute what an
arm does not find.

The cause is the arm's query formulation, not the idea. `websearch_to_tsquery` applies
AND semantics over stemmed terms, so *"For Kavya Pillai, what peak throughput was
sustained and by how much was p99 latency cut?"* requires every stem to be present and
matches nothing. The fix is to stop handing the lexical arm a conversational sentence:
extract the distinctive terms first, or rank with `plainto_tsquery` instead of
filtering. Until then the arm is dead weight, and the honest report is that **the
hybrid configuration does not justify its complexity on this query distribution.**

Keeping D in the table with a null result is the point. An ablation that only reports
the arms that helped is a sales deck.

## Finding 6 — better retrieval made abstention *worse*

The trade-off C pays for its gains:

| | B | C |
| --- | --- | --- |
| over-refusal | 0.227 | **0.000** |
| correct abstention | **0.875** | 0.500 |
| citation precision | **0.944** | 0.846 |

Enrichment stopped the system refusing answerable questions — and started it answering
unanswerable ones. Retrieval now surfaces a plausible-looking passage for almost any
query, including the denial near-misses, and the generator answers rather than reading
the negation.

This is the same failure as Finding 1, amplified: improving retrieval increased the
*opportunity* to be wrong. It is a genuine tension rather than a regression to patch
away — a system that retrieves nothing never hallucinates and is useless. The candidate
fixes are the ones Finding 1 already named: an entailment check between claim and cited
span, or explicit handling of negation in the prompt. Both are now measurable against
two arms rather than one.

## Finding 7 — lexical-only fails in the worst possible direction

Row L refuses **every** answerable question (over-refusal 1.000) while answering half
the unanswerable ones. The four it answers are the denial near-misses — the passages
where "settlement" and "reconciliation" appear most strongly. Exactly backwards, and a
compact illustration of why exact-term matching alone is not retrieval.

## Where this leaves the table

Recommended configuration: **C — dense + contextual enrichment.** D costs an extra
query for no measurable gain; B is materially worse on every retrieval metric.

Caveats stated plainly:

* **One run per arm.** `gemini-2.5-pro` is non-deterministic and `--runs` now exists;
  these numbers have no spread and should not be quoted as though they do.
* **Recall is approaching saturation again** at 0.955. The corpus needs to grow past
  40 before recall is informative once more.
* **No reranker arm yet**, and no query-transformation arm. Both remain in the plan.

---

# Phase 3, continued — fixing the lexical arm, and cutting the cost

## The cost problem

The first ablation made roughly 400 paid calls, about 120 of them generation. Most were
waste: **retrieval metrics are computed from the evidence bundle, which exists before
the generator runs**, so paying for generation to measure retrieval bought nothing. And
every configuration re-embedded the same forty documents.

Two changes, both structural:

* **Retrieval-only is the default.** `insightlm-eval` no longer calls the generator
  unless `--generate` is passed. Generation metrics print as `n/a`, never `0.00` — an
  unmeasured refusal rate does not exist, and printing a number invites someone to
  report it.
* **A content-addressed embedding cache** (`eval/.cache/embeddings.sqlite`). Identical
  text under the same model is embedded once, ever. Harness-only: a cross-tenant cache
  in the request path would leak, through timing, whether text had been embedded
  before.

Result: the Gemini ablation below cost **358 texts embedded and 0 generation calls**.
Re-running it costs nothing.

A consequence worth stating: identical input produces identical embeddings, so
**retrieval is deterministic and never needs `--runs`**. Two offline runs produced
byte-identical metrics. Only generation has variance worth paying to measure, so the
CLI now refuses `--runs` without `--generate` rather than silently paying for
repetitions that cannot differ.

## Finding 8 — the lexical arm was broken, not weak

Finding 5 reported that hybrid fusion added nothing because the lexical arm returned
nothing. The cause was `websearch_to_tsquery`, which applies AND semantics: every
stemmed term must appear in one passage. No passage contains a dozen terms from a
conversational question.

Replaced with an OR of the question's distinctive terms, ranked by `ts_rank_cd` — a
ranking rather than a boolean filter. Terms are reduced to lowercase alphanumerics
before being joined, because binding stops SQL injection but not *tsquery* injection:
`&`, `|`, `!`, `<->` and `:*` are operators inside the query language, and a malformed
expression is a 500. A property test over 300 random strings pins that.

| Lexical arm | R@8 | nDCG | MRR |
| --- | --- | --- | --- |
| AND semantics | 0.000 | 0.000 | 0.000 |
| **OR, ranked** | **0.955** | **0.965** | **1.000** |

Finding 5's null result was therefore a bug in one arm masquerading as a finding about
fusion. Worth recording precisely because it looked like a legitimate negative result.

## Finding 9 — a weak arm dilutes RRF

Offline, with the hashing embedder:

| | R@8 | nDCG |
| --- | --- | --- |
| C — dense + context | 0.826 | 0.560 |
| **L — lexical only** | **0.955** | **0.965** |
| D — hybrid RRF | 0.939 | 0.885 |

Lexical alone beats the fusion. RRF gives each arm an equal vote, so a weak arm pulls
a strong one down. That is a property of unweighted RRF, not a bug — and it is the
case for weighted fusion if arms of very different quality are ever combined.

## Finding 10 — with a real embedder, the benchmark has saturated

Gemini embeddings, retrieval-only:

| | R@8 | nDCG | MRR |
| --- | --- | --- | --- |
| B — dense | 0.773 | 0.469 | 0.373 |
| C — dense + context | 0.955 | 0.965 | 1.000 |
| D — hybrid RRF | 0.955 | 0.965 | 1.000 |
| L — lexical only | 0.955 | 0.965 | 1.000 |

Three genuinely different methods — semantic, lexical, and their fusion — produce
**identical** numbers, down to the per-kind breakdown. When distinct methods converge
exactly, the benchmark has stopped discriminating between them. The ablation cannot
say whether fusion helps, because both arms are individually already at the ceiling
this dataset allows. Fusion's value is robustness — rescuing items where one arm fails
and the other succeeds — and here they fail on the *same* items.

It does settle Finding 9: with a strong dense arm, fusion is neutral rather than
harmful. The offline result was an artefact of the weak embedder.

### The ceiling is partly a synthetic-data artefact

Every arm misses the same two multi-hop items, retrieving one of their two facts but
not the other. Both facts sit in separate chunks, and the missed one is always the
latency chunk. The generator renders that fact from one template —
*"by removing a synchronous call from the write path"* — so it appears **verbatim in
all forty CVs**. Each candidate's latency chunk competes against thirty-nine
near-duplicates that differ only in a number.

Real CVs do not share boilerplate verbatim. This is a flaw in the evaluation, not in
the pipeline, and it would be easy to misreport as a retrieval limitation. The fix is
in the generator: vary the phrasing per candidate.

## Finding 11 — with realistic paraphrase, fusion hurts

The generator now renders each fact from one of four phrasings, so no sentence is
shared verbatim across CVs. Retrieval-only, Gemini embeddings, 87 texts embedded,
zero generation calls:

| | R@8 | nDCG | MRR |
| --- | --- | --- | --- |
| **C — dense + context** | **1.000** | **1.000** | **1.000** |
| D — hybrid RRF | 0.955 | 0.811 | 0.763 |
| L — lexical only | 0.773 | 0.457 | 0.364 |

The benchmark discriminates again, and the answer is the opposite of the plan's
assumption. Once questions and passages stop sharing wording -- "cut p99 latency"
against "brought tail latency down" -- lexical search degrades exactly as expected,
and unweighted RRF hands that degraded arm an equal vote. Fusion then pulls a
perfect dense ranking down to 0.811 nDCG.

Finding 9 showed the same mechanism with the arms reversed. Together they say one
thing: **unweighted RRF is only as good as its weaker arm allows.** It is not a
free improvement, and the plan's premise that hybrid retrieval would help was wrong
for this corpus.

Two caveats. C at a perfect score means the benchmark has saturated *for C*; harder
items are needed before C can be compared with anything that might beat it. And the
lexical arm's case -- exact terms like `PCI-DSS` -- is under-represented here; a
query set weighted toward exact identifiers could still favour fusion, and weighted
RRF is the obvious thing to try if it does.

## Recommended configuration

**C — dense + contextual enrichment**, now with evidence rather than by default. D matches it at the cost
of a second query, and L matches it only because this benchmark cannot tell them
apart. Revisit once the benchmark can discriminate.

## What Phase 3 inherits

The ablation table now has two arms and a control. Phase 3 adds hybrid + RRF, the
cross-encoder reranker, query transformation and contextual enrichment, and each one
is measured against these recorded numbers rather than argued for.

Three things to fix or grow first:

1. **The corpus is too small.** Recall is saturated and nDCG is noisy across 10
   answerable items. Growing to ~200 documents is the prerequisite for the ablation
   table meaning anything.
2. **Runs are single-shot.** docs/03 §5 calls for ≥3 runs per configuration with
   reported spread. With `gemini-2.5-pro` non-deterministic, a single run cannot
   distinguish a real effect from variance.
3. **Faithfulness is not yet measured.** Citation *precision* is computed exactly from
   labels; whether the cited passage actually *entails* the claim needs a judge behind
   a port, validated against human labels on a subset before its scores are trusted.

---

# Phase 4 follow-up: measuring the criterion judge

The assessment design (ADR-0010) depends on a judge that reads meaning, not wording. Until
now that was asserted, not measured. `insightlm-eval-judge` runs judges over **judge-v1**:
23 hand-written cases in five groups, each probing one failure mode. Every case lists the
verdicts a careful reader would accept and which passage actually supports the criterion,
so a right verdict citing the wrong passage still counts as a citation miss.

```
judge                  acc  detect  cite   direct paraphrase mention negation unrelated
offline (overlap)      48%    57%    69%     100%     20%      25%      0%     100%
gemini-2.5-pro        100%   100%   100%     100%    100%     100%    100%     100%
```

*23 paid calls, cached in `eval/.cache/judgments.json`; reruns are free until a case,
the prompt or the model changes. 19 Sep 2026.*

### Finding 12 — the baseline fails exactly where it was expected to; the model doesn't

The term-overlap baseline is perfect when a CV uses the criterion's own words and blind
otherwise. It says **strong** to "Has never used Kubernetes" (0% on negation) and
**absent** to "Ran our EKS clusters and wrote the Helm charts" (20% on paraphrase). Gemini
gets every case right and cites the supporting passage every time. This is the measured
reason the offline judge is labelled a baseline in the UI, and the reason a model judge
is worth its per-criterion cost.

**What this doesn't show.** The passages are single clean sentences. 100% means the model
reads negation and paraphrase; it doesn't mean it is flawless on a real two-page CV. The
set has also hit its ceiling: it can no longer tell two prompts or two models apart.
**judge-v2** needs harder cases: long passages, mixed signals ("led the migration, then
handed it over"), and seniority judgments.

### Finding 13 — the first live call found a contract bug the offline tests couldn't

The first Gemini run crashed on `confidence: 5`. The prompt never said what scale
confidence was on, and the response schema didn't bound it. Reading the adapter turned up
a worse sibling: an unparsable response was silently replaced with an **absent** verdict,
which would quietly mark a candidate as lacking a skill. Both are fixed: the prompt
defines the scale, the schema enforces `0 ≤ confidence ≤ 1`, and a judge that can't
answer now raises `UpstreamError` (502) instead of inventing a result. A regression test
covers all three malformed shapes.

The lesson is about test strategy, not Gemini. Offline adapters prove the domain logic
and cost nothing, but a provider contract is only verified by calling the provider.
A small cached benchmark like this one is the cheapest way to keep doing that.
