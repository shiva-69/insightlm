# 08 — Threat model (STRIDE)

Scope: the API, the worker, the web proxy, Postgres, Redis, object storage, and the
model providers, as deployed by `compose.yaml`. Every control below names the code or
test that enforces it. A control without a test is listed as a gap, not a control.

## Assets, in order of harm if lost

1. **Candidate data**: CVs, extracted text, embeddings, assessments, scorecards,
   self-identification (special-category data).
2. **Hiring decisions and their record**: rubrics, stage history, the audit log.
3. **Workspace credentials**: passwords, access tokens, invitation tokens.
4. **Model spend**: the provider keys and the budget they draw on.

## Trust boundaries

```
browser ──► web proxy (Next) ──► API ──► Postgres (RLS) / Redis / object store
                                  │
                                  └─► outbox ──► queue ──► worker ──► model providers
candidate-authored CV text  ═══════════════════════════════╝  (untrusted, reaches models)
```

The unusual boundary is the last one: **the people being evaluated write the input the
models read.** Most of the Tampering rows follow from that.

## Spoofing

| Threat | Control | Enforced by |
| --- | --- | --- |
| Password guessing / credential stuffing | Argon2id hashes; 10 sign-in attempts per minute per client IP | `identity.adapters.security`; `tests/integration/test_limits.py::test_sign_in_attempts_are_limited_per_client` |
| Forged or expired access token | HS256 JWT, 15-minute expiry, required claims; every protected route rejects anonymous and forged tokens | `tests/security/test_api_authorisation.py` (sweeps every endpoint) |
| Spoofing the client IP to dodge limits | IP header set only by the proxy, stripped from inbound requests, trusted only when configured | `web/app/api/[...path]/route.ts`; `test_a_forged_client_ip_header_is_ignored_when_the_proxy_isnt_trusted` |
| Replaying an invitation | Single-use token, hash stored, expiry | `tests/integration/test_identity_flow.py` |

## Tampering

| Threat | Control | Enforced by |
| --- | --- | --- |
| **Hidden text in a CV** (white, 1pt, transparent, off-page) inflating a score | Concealed text excluded at parse time and shown to reviewers | ADR-0012; `test_the_redteam_corpus_holds`; Finding 15 (lift +28 → 0) |
| **Delimiter injection** (`</source><system>…`) | All passage text and attributes HTML-escaped in prompts | `test_a_source_cannot_close_its_own_tag_or_open_a_system_one` |
| Instructions addressed to the model | Prompts treat sources as data; pattern scan flags for reviewers; citation contract discards uncited findings | Findings 14–15; `test_a_judge_citing_nothing_real_is_downgraded_to_absent` |
| Editing or deleting audit history | Append-only grants, trigger, hash chain verified on demand | `tests/security/test_audit_tamper_evidence.py` |
| Rewriting stage history | App role has no UPDATE on `stage_transitions` | migration `0004_pipeline` |
| Retried write creating duplicates | Idempotency keys on every mutating endpoint | `test_a_retried_write_with_the_same_key_happens_once` |

## Repudiation

| Threat | Control | Enforced by |
| --- | --- | --- |
| "I never approved that rubric / moved that candidate" | Every state change is an audit entry with actor and time; approval records who and when | audit assertions in `test_recruiting_flow.py`, `test_pipeline_flow.py` |
| "That score came from somewhere else" | Assessments store model, prompt version, retrieval config hash and redaction profile | `test_every_verdict_cites_the_candidates_own_documents` |

## Information disclosure

| Threat | Control | Enforced by |
| --- | --- | --- |
| One workspace reading another's data | Postgres RLS on every tenant table, restricted app role (NOBYPASSRLS) | `tests/security/test_tenant_isolation.py`; cross-tenant 404s in recruiting tests |
| Probing for another workspace's ids | 404 rather than 403 wherever existence is itself information | `test_another_tenants_documents_are_indistinguishable_from_missing` |
| An interviewer seeing other candidates or scores | Assignment-scoped visibility; interviewers never see AI scores | `test_an_unassigned_interviewer_cannot_see_the_application` |
| Anchoring: seeing others' ratings before writing your own | Ratings withheld until submission | `test_a_panel_member_who_has_not_submitted_sees_no_one_elses_ratings` |
| The model learning who the candidate is | Read-time redaction of name, contact, institution, demographics, pronouns | ADR-0013; `test_a_blind_judge_never_sees_who_the_candidate_is` |
| Identity in the permanent audit log | No titles, checksums or names written; verified on every erasure | ADR-0015; erasure residue search |
| Self-identification leaking into review | Separate table, owner/auditor only, never joined into review or model input | `test_the_report_counts_screening_decisions_by_self_identified_group` |
| Error messages leaking internals | Unhandled errors return a generic message and a correlation id; detail only in logs | `api/errors.py` |
| Data kept longer than needed | Retention through verified erasure | `test_retention_erases_only_rejected_candidates_past_the_period` |

## Denial of service

| Threat | Control | Enforced by |
| --- | --- | --- |
| Request floods | Per-user and per-workspace rate limits (Redis) | `test_limits.py` |
| Draining the model budget | Per-workspace daily cap, charged before any call | `test_over_budget_assessment_is_refused_before_any_model_call` |
| Pathological input crashing handlers | Bounded pagination; unstorable text refused; schemathesis finds no 5xx in 3,183 generated requests | `tests/security/test_input_hygiene.py`; `make fuzz` |
| A poison document blocking the queue | Permanent failures dead-letter at once; transient ones retry with backoff | `tests/integration/test_ingestion_failures.py` |
| Provider outage | Retries with jitter on 5xx/429; typed `UpstreamError` rather than a fabricated answer | Findings 13, 16 |

## Elevation of privilege

| Threat | Control | Enforced by |
| --- | --- | --- |
| Acting beyond one's role | Permission table checked in every use case; the UI hides what the server would refuse | `tests/unit/test_policy.py`; 403 tests throughout |
| Setting your own bar | Proposer cannot approve their own rubric (owner excepted, by design) | `test_a_proposer_cannot_approve_their_own_rubric` |
| Inviting someone as owner | Ownership is not invitable | identity use cases |
| App compromise reaching other tenants' rows | RLS still applies to the app role; the relay's cross-tenant exemption is limited to the outbox | ADR-0016 |

## Known gaps

Stated so they are decisions, not surprises:

- **Closed:** browser sessions used to keep the token in `sessionStorage`, readable by
  any script on the page. It now lives in an httpOnly, Secure, SameSite=Lax cookie, and
  cookie-authenticated writes must echo a CSRF cookie in `X-CSRF-Token`
  (`api/session.py`, `tests/security/test_session_cookies.py`). Bearer tokens remain
  for API clients.
- **Closed:** there was no Content Security Policy. Pages now carry a strict per-request
  nonce policy (`web/proxy.ts`): only nonce-bearing scripts run, data goes only to this
  origin, and nothing may frame the app. API responses forbid all content and framing.
- **Closed:** there was no second factor. Users can turn on authenticator-app codes
  (TOTP, RFC 6238); secrets are encrypted at rest with AES-GCM, enrolment needs a
  working code, each code is accepted once, and owners are urged to enable it
  (`identity/domain/totp.py`, `tests/security/test_mfa.py`). Account lockout is still
  only the per-IP sign-in rate limit.
- **Text hidden under a shape drawn on top of it** isn't detected; scanned CVs skip the
  hidden-text check until OCR exists.
- **Paraphrased instructions** evade the pattern scan (2 of 24 red-team cases). They're
  harmless in the measured cases, but nothing flags them.
- **Secrets come from environment variables,** not a secret manager.
- **The API port is published in `compose.yaml`** for local development, which is why
  trusting the proxy's IP header there is a development convenience only.
- **Object storage is not encrypted by the application;** it relies on the store's own
  encryption at rest.
