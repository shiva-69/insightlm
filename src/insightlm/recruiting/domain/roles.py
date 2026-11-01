"""Hiring roles and what each may do. Mirrors the table in docs/04 SS1.

The recruiting vertical declares its own vocabulary; the kernel only supplies the
mechanism and the owner and auditor, who receive these permissions automatically --
the owner all of them, the auditor the read-only ones.
"""

from __future__ import annotations

from insightlm.documents.domain.permissions import DocumentPermission
from insightlm.shared.domain.roles import Permission, Role, RoleDefinition


class RecruitingPermission:
    REQUISITION_MANAGE = Permission("requisition.manage")
    RUBRIC_PROPOSE = Permission("rubric.propose")
    RUBRIC_APPROVE = Permission("rubric.approve")
    ASSESSMENT_RUN = Permission("assessment.run")
    STAGE_ADVANCE = Permission("stage.advance")
    SCORE_VIEW = Permission("score.view", read_only=True)
    SCORECARD_SUBMIT = Permission("scorecard.submit")
    CANDIDATE_VIEW = Permission("candidate.view", read_only=True)
    SCHEDULE_MANAGE = Permission("schedule.manage")


RECRUITING_PERMISSIONS: tuple[Permission, ...] = (
    RecruitingPermission.REQUISITION_MANAGE,
    RecruitingPermission.RUBRIC_PROPOSE,
    RecruitingPermission.RUBRIC_APPROVE,
    RecruitingPermission.ASSESSMENT_RUN,
    RecruitingPermission.STAGE_ADVANCE,
    RecruitingPermission.SCORE_VIEW,
    RecruitingPermission.SCORECARD_SUBMIT,
    RecruitingPermission.CANDIDATE_VIEW,
    RecruitingPermission.SCHEDULE_MANAGE,
)


class RecruitingRole:
    RECRUITER = Role("recruiter")
    HIRING_MANAGER = Role("hiring_manager")
    INTERVIEWER = Role("interviewer")
    COORDINATOR = Role("coordinator")


_P = RecruitingPermission
RECRUITING_ROLES: tuple[RoleDefinition, ...] = (
    RoleDefinition(
        RecruitingRole.RECRUITER,
        "Recruiter",
        "Sources candidates, proposes rubrics, moves people through stages",
        frozenset(
            {
                DocumentPermission.UPLOAD,
                DocumentPermission.READ,
                _P.REQUISITION_MANAGE,
                _P.RUBRIC_PROPOSE,
                _P.ASSESSMENT_RUN,
                _P.STAGE_ADVANCE,
                _P.SCORE_VIEW,
                _P.SCORECARD_SUBMIT,
                _P.CANDIDATE_VIEW,
                _P.SCHEDULE_MANAGE,
            }
        ),
    ),
    RoleDefinition(
        RecruitingRole.HIRING_MANAGER,
        "Hiring manager",
        "Approves rubrics and makes the call",
        frozenset(
            {
                DocumentPermission.READ,
                _P.RUBRIC_APPROVE,
                _P.ASSESSMENT_RUN,
                _P.STAGE_ADVANCE,
                _P.SCORE_VIEW,
                _P.SCORECARD_SUBMIT,
                _P.CANDIDATE_VIEW,
            }
        ),
    ),
    RoleDefinition(
        RecruitingRole.INTERVIEWER,
        "Interviewer",
        "Submits scorecards; cannot see others' before their own",
        frozenset({DocumentPermission.READ, _P.SCORECARD_SUBMIT, _P.CANDIDATE_VIEW}),
    ),
    RoleDefinition(
        RecruitingRole.COORDINATOR,
        "Coordinator",
        "Scheduling only",
        frozenset({_P.CANDIDATE_VIEW, _P.SCHEDULE_MANAGE}),
    ),
)
