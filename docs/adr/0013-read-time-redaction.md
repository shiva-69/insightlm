# ADR-0013: Read-time redaction for blind screening, not write-time

- **Status:** Accepted
- **Date:** 2026-09-19

## Context

Blind screening means the model judging a criterion never learns who the candidate is:
their name, contact details, institution, demographic lines, gendered pronouns. The
question is *where* identity is removed. The reviewer still needs the real CV, citations
must still point at real text, and the same document may be assessed blind for one
requisition and not for another.

## Options considered

| Option | Pros | Cons |
| --- | --- | --- |
| Redact at ingestion and store the redacted text | One place; index never sees identity | Reviewers lose the real CV; citations point at "[candidate]"; a redaction bug is permanent; can't turn blind screening off per role |
| Store both versions | Either is available | Twice the text and index; two copies to erase; the two drift |
| **Redact at read time, when evidence is handed to a judge** | Stored text, reviewer view and citations untouched; per-requisition switch; a better rule applies to past documents instantly | Must be applied at every model boundary, and nothing may bypass it |

## Decision

`governance.domain.redaction` is a pure function over text and a profile (`blind-v1`,
or `none`). `RunAssessment` applies it to a *copy* of each evidence bundle just before the
judge sees it: to the passage text **and every other rendered field** (document title,
section label). Labels are shared between the copy and the original, so the judge cites
`C2` blind and `_resolve` quotes the original `C2` to the reviewer. The candidate's name
comes from the candidate record, not from name detection. Blind screening is a
per-requisition flag, on by default, and each assessment records the profile it ran under.

## Rationale

Identity has to be absent from the *model's* view, not from the product. Read-time
redaction does that without degrading anything a person sees, and keeps one copy of the
data to protect and erase.

## Consequences

- The integration test uses a spy judge that records exactly what the model received. On
  its first run it found the name leaking through the section label (the chunker reads
  the name at the top of a CV as a heading). Every rendered field is now redacted.
- Measured effect (docs/07, Finding 17): no name, institution or city effect was
  distinguishable from the judge's noise either way, and blind screening cut the judge's
  run-to-run noise on an ambiguous CV from sd 10.9 to 2.4.
- Known gaps: locations are deliberately not redacted; institution matching is
  pattern-based and missed "IGNOU" and left the town after "Government Engineering
  College, Thrissur".

## Reversal

If redaction had to cover a second model boundary we don't control (e.g. a hosted
reranker), the same function would be applied there. We'd revisit write-time redaction
only if a regulator required that identity never be *stored* alongside assessments,
which is a different requirement from not showing it to a model.
