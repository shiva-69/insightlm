"""Workspace membership: inviting teammates and managing their roles."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from insightlm.api.deps import ActorDep, ContainerDep
from insightlm.api.schemas import (
    ChangeRoleRequest,
    InvitationResponse,
    InviteMemberRequest,
    MemberResponse,
    MembershipResponse,
    UserResponse,
)
from insightlm.identity.application.commands import InviteMemberCommand
from insightlm.shared.domain.ids import MembershipId

router = APIRouter(prefix="/members", tags=["members"])


@router.get("", response_model=list[MemberResponse], summary="List workspace members")
async def list_members(actor: ActorDep, container: ContainerDep) -> list[MemberResponse]:
    views = await container.list_members.execute(actor)
    return [
        MemberResponse(
            membership=MembershipResponse.model_validate(view.membership),
            user=UserResponse.model_validate(view.user),
        )
        for view in views
    ]


@router.post(
    "/invitations",
    response_model=InvitationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Invite someone to help with recruiting",
)
async def invite_member(
    request: InviteMemberRequest, actor: ActorDep, container: ContainerDep
) -> InvitationResponse:
    result = await container.invite_member.execute(
        actor, InviteMemberCommand(email=str(request.email), role=request.role)
    )
    return InvitationResponse(
        id=result.invitation.id,
        email=result.invitation.email,
        role=result.invitation.role,
        expires_at=result.invitation.expires_at,
        token=result.token,
    )


@router.patch(
    "/{membership_id}/role",
    response_model=MembershipResponse,
    summary="Change a member's role",
)
async def change_role(
    membership_id: UUID,
    request: ChangeRoleRequest,
    actor: ActorDep,
    container: ContainerDep,
) -> MembershipResponse:
    membership = await container.change_member_role.execute(
        actor, MembershipId(membership_id), request.role
    )
    return MembershipResponse.model_validate(membership)


@router.delete(
    "/{membership_id}",
    response_model=MembershipResponse,
    summary="Revoke a membership",
)
async def revoke_membership(
    membership_id: UUID, actor: ActorDep, container: ContainerDep
) -> MembershipResponse:
    membership = await container.revoke_membership.execute(actor, MembershipId(membership_id))
    return MembershipResponse.model_validate(membership)
