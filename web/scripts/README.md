# scripts

## `walkthrough.mjs`

Drives a real browser through the whole product — register, upload, wait for
ingestion, ask, click a citation, invite a teammate, verify the audit chain — and
writes a screenshot at each step.

It is not a test: it asserts almost nothing. It exists because UI regressions are
the kind that a green test suite happily reports as fine. Two defects were caught
by looking at its output that nothing else would have surfaced: fonts silently
falling back to the system stack, and claims arriving as mid-sentence fragments.

```bash
# with the API, worker and `npm run dev` already running
npm run walkthrough -- /tmp/shots /path/to/a.pdf
```

Phase 8 turns this into the scripted demo path, with assertions.
