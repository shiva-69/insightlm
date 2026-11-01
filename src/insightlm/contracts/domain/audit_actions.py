"""The audit actions contract review records."""

from __future__ import annotations

from insightlm.governance.domain.audit import AuditAction


class ContractAudit:
    PLAYBOOK_PROPOSED = AuditAction("playbook.proposed")
    PLAYBOOK_APPROVED = AuditAction("playbook.approved")
    CONTRACT_REVIEWED = AuditAction("contract.reviewed")
