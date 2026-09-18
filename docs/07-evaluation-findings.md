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
