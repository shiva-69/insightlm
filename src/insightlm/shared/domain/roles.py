"""Roles and the permission matrix.

The matrix is data, not scattered ``if`` statements, so it can be read in one place,
asserted in one test, and printed into the dissertation as-is.
Mirrors the table in docs/04 SS1.
"""

from __future__ import annotations

from collections.abc import Mapping
from enum import StrEnum
from types import MappingProxyType


class Role(StrEnum):
    OWNER = "owner"
    RECRUITER = "recruiter"
    HIRING_MANAGER = "hiring_manager"
    INTERVIEWER = "interviewer"
    COORDINATOR = "coordinator"
    AUDITOR = "auditor"


class Permission(StrEnum):
    # tenant + membership
    TENANT_MANAGE = "tenant.manage"
    MEMBER_INVITE = "member.invite"
    MEMBER_MANAGE = "member.manage"
    AUDIT_READ = "audit.read"
    # documents
    DOCUMENT_UPLOAD = "document.upload"
    DOCUMENT_READ = "document.read"
    # recruiting (permissions defined now, enforced when the module lands in Phase 4)
    REQUISITION_MANAGE = "requisition.manage"
    RUBRIC_PROPOSE = "rubric.propose"
    RUBRIC_APPROVE = "rubric.approve"
    ASSESSMENT_RUN = "assessment.run"
    STAGE_ADVANCE = "stage.advance"
    SCORE_VIEW = "score.view"
    SCORECARD_SUBMIT = "scorecard.submit"
    CANDIDATE_VIEW = "candidate.view"
    SCHEDULE_MANAGE = "schedule.manage"


_OWNER: frozenset[Permission] = frozenset(Permission)

_RECRUITER: frozenset[Permission] = frozenset(
    {
        Permission.DOCUMENT_UPLOAD,
        Permission.DOCUMENT_READ,
        Permission.REQUISITION_MANAGE,
        Permission.RUBRIC_PROPOSE,
        Permission.ASSESSMENT_RUN,
        Permission.STAGE_ADVANCE,
        Permission.SCORE_VIEW,
        Permission.SCORECARD_SUBMIT,
        Permission.CANDIDATE_VIEW,
        Permission.SCHEDULE_MANAGE,
    }
)

_HIRING_MANAGER: frozenset[Permission] = frozenset(
    {
        Permission.DOCUMENT_READ,
        Permission.RUBRIC_APPROVE,
        Permission.ASSESSMENT_RUN,
        Permission.STAGE_ADVANCE,
        Permission.SCORE_VIEW,
        Permission.SCORECARD_SUBMIT,
        Permission.CANDIDATE_VIEW,
    }
)

_INTERVIEWER: frozenset[Permission] = frozenset(
    {
        Permission.DOCUMENT_READ,
        Permission.SCORECARD_SUBMIT,
        Permission.CANDIDATE_VIEW,
    }
)

_COORDINATOR: frozenset[Permission] = frozenset(
    {
        Permission.CANDIDATE_VIEW,
        Permission.SCHEDULE_MANAGE,
    }
)

# Read-only by construction: an auditor who can write is not an auditor.
_AUDITOR: frozenset[Permission] = frozenset(
    {
        Permission.AUDIT_READ,
        Permission.DOCUMENT_READ,
        Permission.SCORE_VIEW,
        Permission.CANDIDATE_VIEW,
    }
)

ROLE_PERMISSIONS: Mapping[Role, frozenset[Permission]] = MappingProxyType(
    {
        Role.OWNER: _OWNER,
        Role.RECRUITER: _RECRUITER,
        Role.HIRING_MANAGER: _HIRING_MANAGER,
        Role.INTERVIEWER: _INTERVIEWER,
        Role.COORDINATOR: _COORDINATOR,
        Role.AUDITOR: _AUDITOR,
    }
)

ASSIGNABLE_BY_INVITATION: frozenset[Role] = frozenset(Role) - {Role.OWNER}
"""Ownership transfers explicitly; it is never handed out by invitation."""
