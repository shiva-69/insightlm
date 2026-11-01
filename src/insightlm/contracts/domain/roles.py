"""Who does what in contract review.

Declared by this module and assembled into the role catalogue by the composition root,
exactly like recruiting's roles. The owner and auditor pick these permissions up
automatically -- the owner all of them, the auditor the read-only ones.
"""

from __future__ import annotations

from insightlm.documents.domain.permissions import DocumentPermission
from insightlm.shared.domain.roles import Permission, Role, RoleDefinition


class ContractPermission:
    PLAYBOOK_PROPOSE = Permission("playbook.propose")
    PLAYBOOK_APPROVE = Permission("playbook.approve")
    CONTRACT_REVIEW = Permission("contract.review")
    REVIEW_VIEW = Permission("contract.review_view", read_only=True)


CONTRACT_PERMISSIONS: tuple[Permission, ...] = (
    ContractPermission.PLAYBOOK_PROPOSE,
    ContractPermission.PLAYBOOK_APPROVE,
    ContractPermission.CONTRACT_REVIEW,
    ContractPermission.REVIEW_VIEW,
)


class ContractRole:
    CONTRACT_MANAGER = Role("contract_manager")
    COUNSEL = Role("counsel")


_P = ContractPermission
CONTRACT_ROLES: tuple[RoleDefinition, ...] = (
    RoleDefinition(
        ContractRole.CONTRACT_MANAGER,
        "Contract manager",
        "Uploads contracts, drafts playbooks and runs reviews",
        frozenset(
            {
                DocumentPermission.UPLOAD,
                DocumentPermission.READ,
                _P.PLAYBOOK_PROPOSE,
                _P.CONTRACT_REVIEW,
                _P.REVIEW_VIEW,
            }
        ),
    ),
    RoleDefinition(
        ContractRole.COUNSEL,
        "Counsel",
        "Approves playbooks and decides on flagged contracts",
        frozenset(
            {
                DocumentPermission.READ,
                _P.PLAYBOOK_APPROVE,
                _P.CONTRACT_REVIEW,
                _P.REVIEW_VIEW,
            }
        ),
    ),
)
