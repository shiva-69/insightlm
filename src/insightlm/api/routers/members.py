"""Workspace membership: inviting teammates and managing their roles."""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from pydantic import BaseModel

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
    views = await container.identity.list_members.execute(actor)
    return [
        MemberResponse(
            membership=MembershipResponse.model_validate(view.membership),
            user=UserResponse.model_validate(view.user),
        )
        for view in views
    ]


class RoleResponse(BaseModel):
    role: str
    label: str
    description: str
    assignable: bool
    permissions: list[str]


@router.get(
    "/roles", response_model=list[RoleResponse], summary="The roles this workspace can assign"
)
async def list_roles(actor: ActorDep, container: ContainerDep) -> list[RoleResponse]:
    """Assembled from every module's declarations, so a new vertical's roles appear
    here, with their permissions, without the API or the interface changing."""
    catalogue = container.roles
    return [
        RoleResponse(
            role=d.role.value,
            label=d.label,
            description=d.description,
            assignable=d.assignable,
            permissions=sorted(p.value for p in catalogue.permissions_for(d.role)),
        )
        for d in catalogue.roles
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
    result = await container.identity.invite_member.execute(
        actor,
        InviteMemberCommand(email=str(request.email), role=container.roles.resolve(request.role)),
    )
    return InvitationResponse(
        id=result.invitation.id,
        email=result.invitation.email,
        role=result.invitation.role.value,
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
    membership = await container.identity.change_member_role.execute(
        actor, MembershipId(membership_id), container.roles.resolve(request.role)
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
    membership = await container.identity.revoke_membership.execute(
        actor, MembershipId(membership_id)
    )
    return MembershipResponse.model_validate(membership)
