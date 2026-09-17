# ADR-0004: Structure-aware chunking with persisted geometry

- **Status:** Accepted
- **Date:** 2026-09-18

## Context

Chunking determines what can be retrieved. It is also the decision most often made by
accident: a fixed 1000-character window with 200 of overlap, copied from a tutorial,
never measured.

Two forces specific to this project. First, the citation contract needs every chunk to
know *where it physically sits* in the source, or a citation cannot be highlighted.
Second, résumés are not prose — they are sectioned documents where "EXPERIENCE" and
"EDUCATION" are genuinely different topics.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Fixed-size windows | Trivial; uniform | Splits mid-sentence, severs bullets from headings, blends unrelated sections |
| One chunk per section | Respects structure | A long section exceeds any context budget and dilutes its own embedding |
| **Structure-first, then size-bounded** | Sections are hard boundaries; splits fall on paragraph/sentence edges | More code; boundary rules need testing |

## Decision

Sections are hard boundaries. Within a section, split to a token budget preferring
paragraph breaks, then sentence ends, then whitespace, with overlap. Each chunk keeps
its character range and the merged per-line rectangles covering it. A section heading
is prepended as a `context_header` before embedding but is **not** part of the cited
span.

Chunking lives in `retrieval`, not `documents`, because every parameter here is a
retrieval-quality knob and an arm of the Phase 3 ablation.

## Rationale

**Geometry is captured at parse time or never.** Text and per-character rectangles are
built in one pass and are index-aligned by contract (`ParsedDocument.__post_init__`
refuses to construct otherwise). Extracting text first and locating it afterwards
drifts on ligatures, hyphenation and whitespace normalisation — invisibly, until a
highlight lands on the wrong sentence.

**Only merged rectangles are persisted.** One rectangle per character would be tens of
megabytes of JSONB for a long PDF. Merging to one per visual line gives a highlight
that wraps correctly and costs almost nothing. Re-chunking re-parses from the stored
bytes, which is a rare, explicit operation.

**The context header is embedded but not cited.** "Reduced p99 latency by 40%" is close
to unretrievable alone; prefixed with its section it is findable. Citing the header
would quote text the claim does not rest on.

## Consequences

**Easy:** citations highlight correctly, including across wrapped lines. Section
boundaries stop unrelated content blending. Two-column résumés survive, because
PyMuPDF's block ordering keeps columns separate.

**Hard:** boundary rules need their own tests. Re-chunking costs a re-parse. DOCX has
no fixed layout, so citations there degrade to text excerpts — surfaced as a warning
rather than hidden.

**We now owe:** the ablation in Phase 3 must actually vary these parameters. A knob
that is never turned is a parameter, not a decision.

## Reversal

Trigger: the ablation showing a different strategy wins — semantic chunking on
embedding-similarity boundaries, or late chunking. The `Chunker` port means that is one
adapter plus a re-ingest, and the eval harness proves whether it helped. Estimated
cost: a day, plus reprocessing time.
