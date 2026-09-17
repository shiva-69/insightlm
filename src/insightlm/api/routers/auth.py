"""Registration, login, and invitation acceptance -- the unauthenticated surface."""

from __future__ import annotations

from fastapi import APIRouter, status

from insightlm.api.deps import ContainerDep
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
    result = await container.register_owner.execute(
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


@router.post("/login", response_model=TokenResponse, summary="Exchange credentials for a token")
async def login(request: LoginRequest, container: ContainerDep) -> TokenResponse:
    result = await container.authenticate.execute(
        AuthenticateCommand(
            email=str(request.email),
            password=request.password,
            tenant_slug=request.tenant_slug,
        )
    )
    return TokenResponse(access_token=result.access_token, expires_in=result.expires_in)


@router.post(
    "/invitations/accept",
    response_model=RegistrationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Redeem an invitation token",
)
async def accept_invitation(
    request: AcceptInvitationRequest, container: ContainerDep
) -> RegistrationResponse:
    result = await container.accept_invitation.execute(
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
