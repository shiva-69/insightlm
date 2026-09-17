"""Identity's unit of work.

Extends the shared transaction boundary with the repositories this module owns, plus
the audit log -- because in this system an identity change that is not audited is a
bug, and putting the audit log on the transaction makes that structurally true.
"""

from __future__ import annotations

from typing import Protocol

from insightlm.governance.ports.audit import AuditLog
from insightlm.identity.ports.repositories import (
    InvitationRepository,
    MembershipRepository,
    TenantRepository,
    UserRepository,
)
from insightlm.shared.domain.ids import TenantId, UserId
from insightlm.shared.ports.uow import UnitOfWork


class IdentityUnitOfWork(UnitOfWork, Protocol):
    @property
    def tenants(self) -> TenantRepository: ...

    @property
    def users(self) -> UserRepository: ...

    @property
    def memberships(self) -> MembershipRepository: ...

    @property
    def invitations(self) -> InvitationRepository: ...

    @property
    def audit(self) -> AuditLog: ...


class IdentityUnitOfWorkFactory(Protocol):
    """Creates a unit of work, optionally scoped to a tenant.

    Use cases depend on this rather than on a session factory, so they never see
    SQLAlchemy and the composition root stays the only place adapters are named.
    """

    def __call__(
        self, *, tenant_id: TenantId | None = None, user_id: UserId | None = None
    ) -> IdentityUnitOfWork: ...
