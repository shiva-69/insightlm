"""Registration, login, and invitation acceptance -- the unauthenticated surface."""

from __future__ import annotations

from fastapi import APIRouter, Response, status
from pydantic import BaseModel

from insightlm.api.deps import ActorDep, ContainerDep
from insightlm.api.schemas import (
    AcceptInvitationRequest,
    LoginRequest,
    MembershipResponse,
    RegisterOwnerRequest,
    RegistrationResponse,
    TenantResponse,
    TokenResponse,
    UserResponse,
)
from insightlm.api.session import clear_session_cookies, set_session_cookies
from insightlm.identity.application.commands import (
    AcceptInvitationCommand,
    AuthenticateCommand,
    RegisterOwnerCommand,
)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a workspace and its owner",
)
async def register(request: RegisterOwnerRequest, container: ContainerDep) -> RegistrationResponse:
    result = await container.identity.register_owner.execute(
        RegisterOwnerCommand(
            tenant_name=request.tenant_name,
            tenant_slug=request.tenant_slug,
            email=str(request.email),
            display_name=request.display_name,
            password=request.password,
        )
    )
    return RegistrationResponse(
        tenant=TenantResponse.model_validate(result.tenant),
        user=UserResponse.model_validate(result.user),
        membership=MembershipResponse.model_validate(result.membership),
    )


@router.post(
    "/login",
    response_model=TokenResponse,
    summary="Exchange credentials for a session cookie (browsers) and a token (API clients)",
)
async def login(
    request: LoginRequest, response: Response, container: ContainerDep
) -> TokenResponse:
    result = await container.identity.authenticate.execute(
        AuthenticateCommand(
            email=str(request.email),
            password=request.password,
            tenant_slug=request.tenant_slug,
        )
    )
    set_session_cookies(
        response,
        result.access_token,
        max_age=result.expires_in,
        secure=container.settings.session_cookie_secure,
    )
    return TokenResponse(access_token=result.access_token, expires_in=result.expires_in)


class SessionResponse(BaseModel):
    user_id: str
    tenant_id: str
    role: str
    permissions: list[str]


@router.get(
    "/session",
    response_model=SessionResponse,
    summary="Who the current session belongs to, for shaping the interface",
)
async def session(actor: ActorDep) -> SessionResponse:
    """The browser cannot read its own token any more, so it asks. Shaping the UI only:
    every request is still authorised on the server."""
    return SessionResponse(
        user_id=str(actor.user_id),
        tenant_id=str(actor.tenant_id),
        role=actor.role.value,
        permissions=sorted(p.value for p in actor.permissions),
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, summary="End the browser session")
async def logout(response: Response, container: ContainerDep) -> Response:
    """Clears the cookies. Needs no authentication: ending a session that has already
    expired must still work, and clearing cookies harms no one."""
    clear_session_cookies(response, secure=container.settings.session_cookie_secure)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post(
    "/invitations/accept",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Redeem an invitation token",
)
async def accept_invitation(
    request: AcceptInvitationRequest, container: ContainerDep
) -> RegistrationResponse:
    result = await container.identity.accept_invitation.execute(
        AcceptInvitationCommand(
            token=request.token,
            display_name=request.display_name,
            password=request.password,
        )
    )
    return RegistrationResponse(
        tenant=TenantResponse.model_validate(result.tenant),
        user=UserResponse.model_validate(result.user),
        membership=MembershipResponse.model_validate(result.membership),
    )
