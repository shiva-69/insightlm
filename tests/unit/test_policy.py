"""The permission matrix. Data, assembled from each module's declarations, asserted here."""

from __future__ import annotations

import pytest

from insightlm.bootstrap import build_role_catalogue
from insightlm.documents.domain.permissions import DocumentPermission
from insightlm.recruiting.domain.roles import RecruitingPermission, RecruitingRole
from insightlm.shared.domain.errors import (
    InvariantViolationError,
    PermissionDeniedError,
    ValidationError,
)
from insightlm.shared.domain.ids import new_tenant_id, new_user_id
from insightlm.shared.domain.policy import Actor, can, require
from insightlm.shared.domain.roles import (
    PLATFORM_PERMISSIONS,
    PLATFORM_ROLES,
    Permission,
    PlatformPermission,
    PlatformRole,
    Role,
    RoleCatalogue,
    RoleDefinition,
)

CATALOGUE = build_role_catalogue()
ALL_ROLES = [d.role for d in CATALOGUE.roles]


def actor(role: Role) -> Actor:
    return Actor.of(CATALOGUE, user_id=new_user_id(), tenant_id=new_tenant_id(), role=role)


def test_the_six_roles_are_assembled_from_their_modules() -> None:
    assert {r.value for r in ALL_ROLES} == {
        "owner",
        "recruiter",
        "hiring_manager",
        "interviewer",
        "coordinator",
        "auditor",
    }


def test_owner_holds_every_permission() -> None:
    assert CATALOGUE.permissions_for(PlatformRole.OWNER) == CATALOGUE.permissions


@pytest.mark.parametrize(
    "role",
    [
        RecruitingRole.INTERVIEWER,
        RecruitingRole.COORDINATOR,
        PlatformRole.AUDITOR,
        RecruitingRole.RECRUITER,
    ],
)
def test_only_the_owner_manages_the_workspace(role: Role) -> None:
    assert not can(actor(role), PlatformPermission.TENANT_MANAGE)


def test_auditor_can_read_the_log_and_nothing_else_can_write() -> None:
    """The role's whole purpose is the asymmetry: total read, zero write."""
    auditor = actor(PlatformRole.AUDITOR)
    assert can(auditor, PlatformPermission.AUDIT_READ)
    writes = {p for p in CATALOGUE.permissions if not p.read_only}
    assert not (CATALOGUE.permissions_for(PlatformRole.AUDITOR) & writes)


def test_the_auditor_sees_exactly_what_it_saw_before_the_catalogue() -> None:
    """Pinned so the move from a hand-written matrix changed nobody's access."""
    assert {p.value for p in CATALOGUE.permissions_for(PlatformRole.AUDITOR)} == {
        "audit.read",
        "document.read",
        "score.view",
        "candidate.view",
    }


def test_only_owner_and_auditor_read_the_audit_log() -> None:
    readers = {
        r for r in ALL_ROLES if PlatformPermission.AUDIT_READ in CATALOGUE.permissions_for(r)
    }
    assert readers == {PlatformRole.OWNER, PlatformRole.AUDITOR}


def test_recruiters_propose_rubrics_but_cannot_approve_them() -> None:
    """Separation of duties: the person sourcing candidates does not set the bar."""
    recruiter = actor(RecruitingRole.RECRUITER)
    assert can(recruiter, RecruitingPermission.RUBRIC_PROPOSE)
    assert not can(recruiter, RecruitingPermission.RUBRIC_APPROVE)


def test_interviewers_cannot_advance_candidates() -> None:
    assert not can(actor(RecruitingRole.INTERVIEWER), RecruitingPermission.STAGE_ADVANCE)


def test_a_cross_tenant_actor_is_denied_whatever_the_role() -> None:
    owner = actor(PlatformRole.OWNER)
    assert not can(owner, PlatformPermission.MEMBER_INVITE, tenant_id=new_tenant_id())


def test_require_raises_with_a_useful_detail() -> None:
    with pytest.raises(PermissionDeniedError) as exc:
        require(actor(RecruitingRole.INTERVIEWER), PlatformPermission.MEMBER_INVITE)
    assert exc.value.detail["role"] == "interviewer"
    assert exc.value.detail["permission"] == "member.invite"


def test_ownership_is_never_assignable_by_invitation() -> None:
    assert PlatformRole.OWNER not in CATALOGUE.assignable
    assert frozenset(ALL_ROLES) - {PlatformRole.OWNER} == CATALOGUE.assignable


def test_an_unknown_role_name_is_a_validation_error_and_holds_nothing() -> None:
    with pytest.raises(ValidationError):
        CATALOGUE.resolve("superuser")
    assert CATALOGUE.permissions_for(Role("superuser")) == frozenset()


# -- the property this design exists for ---------------------------------------------


def test_a_new_vertical_needs_no_change_to_the_kernel() -> None:
    """A contract-review vertical declares its own role and permissions. The kernel,
    documents and recruiting are untouched; the owner and auditor pick the new
    permissions up automatically, by kind."""
    review = Permission("contract.review")
    report = Permission("contract.report_view", read_only=True)
    counsel = RoleDefinition(
        Role("counsel"),
        "Counsel",
        "Reviews contracts",
        frozenset({review, report, DocumentPermission.READ}),
    )
    catalogue = RoleCatalogue(
        roles=(*PLATFORM_ROLES, counsel),
        permissions=(*PLATFORM_PERMISSIONS, DocumentPermission.READ, review, report),
    )
    assert review in catalogue.permissions_for(PlatformRole.OWNER)
    assert report in catalogue.permissions_for(PlatformRole.AUDITOR)
    assert review not in catalogue.permissions_for(PlatformRole.AUDITOR)
    assert catalogue.resolve("counsel") == Role("counsel")


def test_granting_an_undeclared_permission_fails_at_startup() -> None:
    rogue = RoleDefinition(Role("rogue"), "Rogue", "", frozenset({Permission("secret.read")}))
    with pytest.raises(InvariantViolationError):
        RoleCatalogue(roles=(*PLATFORM_ROLES, rogue), permissions=PLATFORM_PERMISSIONS)


def test_a_role_declared_twice_fails_at_startup() -> None:
    twice = RoleDefinition(PlatformRole.AUDITOR, "Auditor again", "")
    with pytest.raises(InvariantViolationError):
        RoleCatalogue(roles=(*PLATFORM_ROLES, twice), permissions=PLATFORM_PERMISSIONS)


@pytest.mark.parametrize("bad", ["Contract.Review", "review", "contract review", ".x"])
def test_permission_names_are_area_dot_action(bad: str) -> None:
    with pytest.raises(InvariantViolationError):
        Permission(bad)
