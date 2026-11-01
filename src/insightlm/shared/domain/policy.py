"""Authorisation decisions.

Lives in the shared kernel rather than in ``identity``: every module needs to express
"who is acting, and may they", so the vocabulary has to be something all of them can
depend on without depending on each other. ``identity`` still owns the *data* -- users,
tenants, memberships -- it just no longer owns the words.

Deny by default, in one place. Not decorators scattered across routers -- rubric
approval and scorecard visibility need rules that read as rules.
"""

from __future__ import annotations

from dataclasses import dataclass

from insightlm.shared.domain.errors import PermissionDeniedError
from insightlm.shared.domain.ids import TenantId, UserId
from insightlm.shared.domain.roles import Permission, Role, RoleCatalogue


@dataclass(frozen=True, slots=True)
class Actor:
    """The authenticated principal, always scoped to exactly one tenant.

    Carries the permissions its role resolved to when it was authenticated, so a
    decision needs no lookup table -- and no module has to know which other modules
    declared which permissions.
    """

    user_id: UserId
    tenant_id: TenantId
    role: Role
    permissions: frozenset[Permission]

    @classmethod
    def of(
        cls, catalogue: RoleCatalogue, *, user_id: UserId, tenant_id: TenantId, role: Role
    ) -> Actor:
        return cls(user_id, tenant_id, role, catalogue.permissions_for(role))

    def has(self, permission: Permission) -> bool:
        return permission in self.permissions


def can(actor: Actor, permission: Permission, *, tenant_id: TenantId | None = None) -> bool:
    """Deny by default. A cross-tenant actor is never permitted, whatever the role."""
    if tenant_id is not None and tenant_id != actor.tenant_id:
        return False
    return actor.has(permission)


def require(actor: Actor, permission: Permission, *, tenant_id: TenantId | None = None) -> None:
    """Raise :class:`PermissionDeniedError` unless permitted.

    Callers guarding a resource whose *existence* is privileged should catch this and
    raise ``NotFoundError`` instead -- see ``docs/06 SS6``.
    """
    if not can(actor, permission, tenant_id=tenant_id):
        raise PermissionDeniedError(
            f"role '{actor.role}' lacks '{permission}'",
            detail={"role": actor.role.value, "permission": permission.value},
        )
