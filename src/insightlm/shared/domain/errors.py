"""Domain error hierarchy.

Every error carries a stable ``code`` so the HTTP layer can map it exactly once,
in one exception handler, instead of scattering status codes through use cases.

Note ``NotFoundError`` is what we raise for *both* "does not exist" and "exists but
you may not see it". Distinguishing them leaks the existence of other tenants' data.
"""

from __future__ import annotations


class DomainError(Exception):
    """Base for every expected, modelled failure."""

    code: str = "domain_error"
    http_status: int = 400

    def __init__(self, message: str, *, detail: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class ValidationError(DomainError):
    code = "validation_error"
    http_status = 422


class NotFoundError(DomainError):
    code = "not_found"
    http_status = 404


class ConflictError(DomainError):
    """The request is valid but conflicts with current state (duplicate, race)."""

    code = "conflict"
    http_status = 409


class AuthenticationError(DomainError):
    code = "unauthenticated"
    http_status = 401


class PermissionDeniedError(DomainError):
    """Raised when the actor is known but not allowed.

    Prefer ``NotFoundError`` when even the resource's existence is privileged.
    """

    code = "permission_denied"
    http_status = 403


class InvariantViolationError(DomainError):
    """A domain rule was broken -- indicates a bug, not bad user input."""

    code = "invariant_violation"
    http_status = 500


class UpstreamError(DomainError):
    """A model provider answered, but not with something we can use.

    Surfaced rather than papered over: substituting a default answer (an "absent"
    verdict, an empty answer) would put a fabricated result in front of a person.
    """

    code = "upstream_invalid"
    http_status = 502


class RateLimitedError(DomainError):
    code = "rate_limited"
    http_status = 429


class BudgetExceededError(DomainError):
    """The workspace has used its model-call budget for the period.

    429 rather than 402: nothing is for sale here, the workspace is simply over quota,
    and clients already know to back off on 429.
    """

    code = "budget_exceeded"
    http_status = 429


class ServiceUnavailableError(DomainError):
    """A dependency needed to do this safely is down; retrying later will work."""

    code = "service_unavailable"
    http_status = 503
