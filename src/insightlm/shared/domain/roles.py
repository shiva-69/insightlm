"""Roles, permissions, and the catalogue that joins them.

The kernel owns the *mechanism* -- what a permission is, what a role is, and how a
role's permissions are resolved -- plus the two roles every workspace has whatever it is
used for: the owner, who may do everything, and the auditor, who may read everything and
change nothing. Every other role and permission is declared by the module that needs it
(``recruiting.domain.roles`` declares recruiters and interviewers; ``documents`` declares
upload and read), and the composition root assembles them into one ``RoleCatalogue``.

That is what lets a second vertical arrive without editing this file: it declares its
own roles and permissions, and the catalogue gives the owner and auditor theirs
automatically. The matrix is still data, readable in one place -- it is just assembled
rather than written out here. Mirrors the table in docs/04 SS1.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from insightlm.shared.domain.errors import InvariantViolationError, ValidationError

_PERMISSION = re.compile(r"^[a-z][a-z_]*\.[a-z][a-z_]*$")
_ROLE = re.compile(r"^[a-z][a-z_]*$")


@dataclass(frozen=True, slots=True)
class Permission:
    """Something an actor may do, named ``area.action``.

    ``read_only`` marks permissions that change nothing. The auditor is granted exactly
    those, so a module that adds a read-only permission extends the auditor's view
    without anyone remembering to.
    """

    value: str
    read_only: bool = field(default=False, compare=False)

    def __post_init__(self) -> None:
        if not _PERMISSION.match(self.value):
            raise InvariantViolationError(f"permission '{self.value}' must be named 'area.action'")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class Role:
    value: str

    def __post_init__(self) -> None:
        if not _ROLE.match(self.value):
            raise InvariantViolationError(f"role '{self.value}' must be lower_snake_case")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, slots=True)
class RoleDefinition:
    role: Role
    label: str
    description: str
    grants: frozenset[Permission] = frozenset()
    assignable: bool = True
    """Whether an invitation may hand this role out. Ownership never is."""


class PlatformPermission:
    """Permissions about the workspace itself, which every vertical shares."""

    TENANT_MANAGE = Permission("tenant.manage")
    MEMBER_INVITE = Permission("member.invite")
    MEMBER_MANAGE = Permission("member.manage")
    AUDIT_READ = Permission("audit.read", read_only=True)


class PlatformRole:
    OWNER = Role("owner")
    AUDITOR = Role("auditor")


PLATFORM_PERMISSIONS: tuple[Permission, ...] = (
    PlatformPermission.TENANT_MANAGE,
    PlatformPermission.MEMBER_INVITE,
    PlatformPermission.MEMBER_MANAGE,
    PlatformPermission.AUDIT_READ,
)

PLATFORM_ROLES: tuple[RoleDefinition, ...] = (
    RoleDefinition(
        PlatformRole.OWNER,
        "Owner",
        "Everything, including members, roles and erasure",
        assignable=False,
    ),
    # Read-only by construction: an auditor who can write is not an auditor.
    RoleDefinition(PlatformRole.AUDITOR, "Auditor", "Reads everything, writes nothing"),
)


class RoleCatalogue:
    """Every role and permission the running system knows, and who may do what.

    Built once, in the composition root, from each module's declarations. The owner is
    granted every permission and the auditor every read-only one; other roles get what
    their module grants them. Construction fails loudly on a duplicate role or a grant
    of an undeclared permission, so a mistake surfaces at startup and in the tests
    rather than as a silent denial in production.
    """

    def __init__(self, roles: Iterable[RoleDefinition], permissions: Iterable[Permission]) -> None:
        self._permissions = frozenset(permissions)
        names = [p.value for p in self._permissions]
        if len(set(names)) != len(names):
            raise InvariantViolationError("a permission is declared twice")

        definitions: dict[Role, RoleDefinition] = {}
        for definition in roles:
            if definition.role in definitions:
                raise InvariantViolationError(f"role '{definition.role}' is declared twice")
            undeclared = definition.grants - self._permissions
            if undeclared:
                raise InvariantViolationError(
                    f"role '{definition.role}' is granted undeclared permissions",
                    detail={"permissions": sorted(p.value for p in undeclared)},
                )
            definitions[definition.role] = definition
        for required in (PlatformRole.OWNER, PlatformRole.AUDITOR):
            if required not in definitions:
                raise InvariantViolationError(f"the catalogue needs the '{required}' role")
        self._definitions = definitions

        read_only = frozenset(p for p in self._permissions if p.read_only)
        matrix: dict[Role, frozenset[Permission]] = {
            role: definition.grants for role, definition in definitions.items()
        }
        matrix[PlatformRole.OWNER] = self._permissions
        matrix[PlatformRole.AUDITOR] = read_only | definitions[PlatformRole.AUDITOR].grants
        self._matrix: Mapping[Role, frozenset[Permission]] = MappingProxyType(matrix)

    @property
    def permissions(self) -> frozenset[Permission]:
        return self._permissions

    @property
    def roles(self) -> tuple[RoleDefinition, ...]:
        return tuple(self._definitions.values())

    @property
    def matrix(self) -> Mapping[Role, frozenset[Permission]]:
        return self._matrix

    @property
    def assignable(self) -> frozenset[Role]:
        return frozenset(r for r, d in self._definitions.items() if d.assignable)

    def permissions_for(self, role: Role) -> frozenset[Permission]:
        """Unknown roles hold nothing: deny by default, never raise on the hot path."""
        return self._matrix.get(role, frozenset())

    def resolve(self, value: str) -> Role:
        """The role named ``value``, or a ValidationError if no module declares it."""
        try:
            role = Role(value)
        except InvariantViolationError as exc:
            raise ValidationError(f"'{value}' is not a role") from exc
        if role not in self._definitions:
            raise ValidationError(
                f"'{value}' is not a role in this system",
                detail={"known": sorted(r.value for r in self._definitions)},
            )
        return role

    def is_known(self, value: str) -> bool:
        try:
            self.resolve(value)
        except ValidationError:
            return False
        return True
