"""Browser sessions: an httpOnly cookie for the token, and a CSRF check to go with it.

The token used to live in the browser's sessionStorage, where any script running on the
page could read it -- one injected script, and the session walks out of the door. Now
the browser holds it in an httpOnly cookie that script cannot see, and sends it with
every same-origin request.

A cookie the browser attaches by itself is exactly what cross-site request forgery
abuses, so cookie-authenticated writes carry a second proof: the value of a readable
``insightlm_csrf`` cookie, echoed in an ``X-CSRF-Token`` header. A page on another origin
can make the browser send the cookies but cannot read them, so it cannot produce the
header. ``SameSite=Lax`` stops most of these requests anyway; the header is the check
that does not depend on browser behaviour.

Bearer tokens still work, unchanged, for scripts and API clients. They need no CSRF
check: a browser never attaches an Authorization header on its own.
"""

from __future__ import annotations

import hmac
import secrets

from fastapi import Request, Response

from insightlm.shared.domain.errors import PermissionDeniedError

SESSION_COOKIE = "insightlm_session"
CSRF_COOKIE = "insightlm_csrf"
CSRF_HEADER = "x-csrf-token"
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def set_session_cookies(response: Response, token: str, *, max_age: int, secure: bool) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        secure=secure,
        samesite="lax",
        path="/",
    )
    # Readable by the page on purpose: it is the page's proof that it is same-origin.
    response.set_cookie(
        CSRF_COOKIE,
        secrets.token_urlsafe(32),
        max_age=max_age,
        httponly=False,
        secure=secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookies(response: Response, *, secure: bool) -> None:
    for name in (SESSION_COOKIE, CSRF_COOKIE):
        response.delete_cookie(name, path="/", secure=secure, samesite="lax")


def credentials(request: Request) -> tuple[str, bool] | None:
    """The token, and whether it came from the session cookie. Bearer wins if both."""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and auth[7:].strip():
        return auth[7:].strip(), False
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return cookie, True
    return None


def require_csrf(request: Request) -> None:
    """Refuse a cookie-authenticated write that does not echo the CSRF cookie."""
    if request.method in _SAFE_METHODS:
        return
    expected = request.cookies.get(CSRF_COOKIE, "")
    supplied = request.headers.get(CSRF_HEADER, "")
    if not expected or not hmac.compare_digest(expected, supplied):
        raise PermissionDeniedError(
            "this request did not prove it came from the InsightLM page",
            detail={"reason": "csrf"},
        )
