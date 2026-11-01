"""FastAPI dependencies: container access and authentication."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from insightlm.api.session import credentials, require_csrf
from insightlm.bootstrap import Container
from insightlm.shared.domain.errors import AuthenticationError
from insightlm.shared.domain.policy import Actor
from insightlm.shared.observability.telemetry import bind_tenant

_bearer = HTTPBearer(auto_error=False, description="Access token from POST /auth/login")


def get_container(request: Request) -> Container:
    container: Container = request.app.state.container
    return container


ContainerDep = Annotated[Container, Depends(get_container)]


async def get_actor(
    request: Request,
    container: ContainerDep,
    _bearer_scheme: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> Actor:
    """Resolve the caller from a bearer token or the browser's session cookie.

    The tenant and role travel in the token, so the hot path needs no database round
    trip. The cost -- a role change takes effect at the next token issue -- is
    acceptable at a 15-minute TTL and is recorded in ADR-0003. A cookie-authenticated
    write must also pass the CSRF check (``api.session``).
    """
    found = credentials(request)
    if found is None:
        raise AuthenticationError("missing bearer token or session")
    token, via_cookie = found
    if via_cookie:
        require_csrf(request)

    claims = container.token_issuer.verify_access_token(token)
    if not container.roles.is_known(claims.role):
        raise AuthenticationError("token carries an unknown role")
    role = container.roles.resolve(claims.role)

    # Labels model-call metrics with the workspace, so cost per tenant is visible.
    bind_tenant(str(claims.tenant_id))
    return Actor.of(container.roles, user_id=claims.user_id, tenant_id=claims.tenant_id, role=role)


ActorDep = Annotated[Actor, Depends(get_actor)]
