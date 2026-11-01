# ADR-0023: Browser sessions in httpOnly cookies, a nonce CSP, and a second factor

- **Status:** Accepted
- **Date:** 2026-11-01

## Context

The threat model (docs/08) listed three gaps in how a browser holds a session. The
token sat in `sessionStorage`, where any script on the page could read it. No Content
Security Policy limited what script could run. And a password was the only factor, so
a leaked password was a lost account, including an owner's, which controls members,
roles and erasure. A tool that reads documents written by outsiders has a larger
injection surface than most, so the first two matter more here than usual.

## Decision

- **Cookies.** Sign-in sets the session token in an `httpOnly`, `Secure`,
  `SameSite=Lax` cookie that no script can read. Bearer tokens stay for API clients, and
  the API accepts either; the browser no longer sends one.
- **CSRF.** A cookie is sent automatically, so a cookie-authenticated write must prove
  same-origin: it echoes a second, readable cookie in `X-CSRF-Token` (double submit,
  compared in constant time). Bearer requests need no such proof, because a browser
  never attaches them on its own.
- **CSP.** Every page carries a per-request nonce policy, set in the Next.js proxy: only
  scripts with the nonce run, data goes only to this origin, and nothing may frame the
  app. API responses carry `default-src 'none'`, so JSON opened in a tab is inert.
- **Two-factor.** Authenticator-app codes (TOTP, RFC 6238), standard library only.
  Secrets are sealed with AES-256-GCM behind a `SecretBox` port. Enrolment needs one
  working code before it takes effect, so a botched setup locks no one out. Each code is
  accepted once. Turning it off needs a current code, and both changes are audited.

## Rationale

Each control closes one specific path, and each has a test that fails without it
(`tests/security/test_session_cookies.py`, `test_mfa.py`). The order matters too.
Cookies alone would have traded token theft for CSRF, and a CSP alone would still leave
a token readable by any script it failed to stop. Together, an injected script can
neither copy the session nor send it anywhere.

TOTP rather than WebAuthn: it works with any authenticator app and needs no new
dependency. WebAuthn is the stronger choice against phishing, and the natural next step.

## Consequences

- The web client reads who is signed in from `GET /auth/session` and caches only that,
  which grants nothing on its own.
- Style attributes stay allowed (`style-src-attr 'unsafe-inline'`), because the document
  viewer positions highlights with them. Injected style can restyle a page but cannot
  run script or send data.
- Account lockout is still only the per-IP sign-in rate limit, and there are no
  recovery codes yet: a user who loses their authenticator needs an owner to reset it.
