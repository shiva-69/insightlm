"""The permission matrix. Data, asserted in one place."""

from __future__ import annotations

import pytest

from insightlm.shared.domain.errors import PermissionDeniedError
from insightlm.shared.domain.ids import new_tenant_id, new_user_id
from insightlm.shared.domain.policy import Actor, can, require
from insightlm.shared.domain.roles import (
    ASSIGNABLE_BY_INVITATION,
    ROLE_PERMISSIONS,
    Permission,
    Role,
)


def actor(role: Role) -> Actor:
    return Actor(user_id=new_user_id(), tenant_id=new_tenant_id(), role=role)


def test_every_role_has_an_entry() -> None:
    assert set(ROLE_PERMISSIONS) == set(Role)


def test_owner_holds_every_permission() -> None:
    assert ROLE_PERMISSIONS[Role.OWNER] == frozenset(Permission)


@pytest.mark.parametrize("role", [Role.INTERVIEWER, Role.COORDINATOR, Role.AUDITOR, Role.RECRUITER])
def test_only_the_owner_manages_the_workspace(role: Role) -> None:
    assert not can(actor(role), Permission.TENANT_MANAGE)


def test_auditor_can_read_the_log_and_nothing_else_can_write() -> None:
    """The role's whole purpose is the asymmetry: total read, zero write."""
    auditor = actor(Role.AUDITOR)
    assert can(auditor, Permission.AUDIT_READ)

    writes = {
        Permission.DOCUMENT_UPLOAD,
        Permission.REQUISITION_MANAGE,
        Permission.RUBRIC_PROPOSE,
        Permission.RUBRIC_APPROVE,
        Permission.ASSESSMENT_RUN,
        Permission.STAGE_ADVANCE,
        Permission.SCORECARD_SUBMIT,
        Permission.MEMBER_INVITE,
        Permission.MEMBER_MANAGE,
    }
    assert not (ROLE_PERMISSIONS[Role.AUDITOR] & writes)


def test_only_owner_and_auditor_read_the_audit_log() -> None:
    readers = {role for role in Role if Permission.AUDIT_READ in ROLE_PERMISSIONS[role]}
    assert readers == {Role.OWNER, Role.AUDITOR}


def test_recruiters_propose_rubrics_but_cannot_approve_them() -> None:
    """Separation of duties: the person sourcing candidates does not set the bar."""
    recruiter = actor(Role.RECRUITER)
    assert can(recruiter, Permission.RUBRIC_PROPOSE)
    assert not can(recruiter, Permission.RUBRIC_APPROVE)


def test_interviewers_cannot_advance_candidates() -> None:
    assert not can(actor(Role.INTERVIEWER), Permission.STAGE_ADVANCE)


def test_a_cross_tenant_actor_is_denied_whatever_the_role() -> None:
    owner = actor(Role.OWNER)
    assert not can(owner, Permission.MEMBER_INVITE, tenant_id=new_tenant_id())


def test_require_raises_with_a_useful_detail() -> None:
    with pytest.raises(PermissionDeniedError) as exc:
        require(actor(Role.INTERVIEWER), Permission.MEMBER_INVITE)
    assert exc.value.detail["role"] == Role.INTERVIEWER.value
    assert exc.value.detail["permission"] == Permission.MEMBER_INVITE.value


def test_ownership_is_never_assignable_by_invitation() -> None:
    assert Role.OWNER not in ASSIGNABLE_BY_INVITATION
    assert frozenset(Role) - {Role.OWNER} == ASSIGNABLE_BY_INVITATION
