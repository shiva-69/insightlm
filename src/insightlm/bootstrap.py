"""The composition root.

This is the *only* module that names both a port and the adapter satisfying it. Every
other module depends on Protocols alone. There is no DI framework: a container is not
needed when there is exactly one wiring site, and one readable wiring site is worth
more than a clever one.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from insightlm.governance.application.audit_queries import ListAuditEntries, VerifyAuditChain
from insightlm.governance.ports.uow import GovernanceUnitOfWork
from insightlm.identity.adapters.security import Argon2PasswordHasher, JwtTokenIssuer
from insightlm.identity.adapters.uow import SqlAlchemyIdentityUnitOfWork
from insightlm.identity.application.authenticate import Authenticate
from insightlm.identity.application.invitations import AcceptInvitation, InviteMember
from insightlm.identity.application.members import (
    ChangeMemberRole,
    ListMembers,
    RevokeMembership,
)
from insightlm.identity.application.register import RegisterOwner
from insightlm.identity.ports.uow import IdentityUnitOfWork
from insightlm.settings import Settings
from insightlm.shared.adapters.clock import SystemClock
from insightlm.shared.adapters.db.base import create_engine, create_session_factory
from insightlm.shared.domain.ids import TenantId, UserId
from insightlm.shared.ports.clock import Clock
from insightlm.shared.ports.password import PasswordHasher
from insightlm.shared.ports.tokens import TokenIssuer


@dataclass(frozen=True, slots=True)
class Container:
    """Everything the delivery layers need, already wired."""

    settings: Settings
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    clock: Clock
    password_hasher: PasswordHasher
    token_issuer: TokenIssuer

    register_owner: RegisterOwner
    authenticate: Authenticate
    invite_member: InviteMember
    accept_invitation: AcceptInvitation
    list_members: ListMembers
    change_member_role: ChangeMemberRole
    revoke_membership: RevokeMembership
    list_audit_entries: ListAuditEntries
    verify_audit_chain: VerifyAuditChain

    async def dispose(self) -> None:
        await self.engine.dispose()


def build_container(settings: Settings) -> Container:
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)

    clock: Clock = SystemClock()
    hasher: PasswordHasher = Argon2PasswordHasher()
    tokens: TokenIssuer = JwtTokenIssuer(
        settings.jwt_secret.get_secret_value(),
        algorithm=settings.jwt_algorithm,
        ttl_seconds=settings.access_token_ttl_seconds,
    )

    def uow_factory(
        *, tenant_id: TenantId | None = None, user_id: UserId | None = None
    ) -> IdentityUnitOfWork:
        return SqlAlchemyIdentityUnitOfWork(
            session_factory, clock, tenant_id=tenant_id, user_id=user_id
        )

    # The same adapter satisfies the narrower governance port -- structural typing
    # means no wrapper is needed, and governance still cannot see identity's repos.
    def governance_uow_factory(
        *, tenant_id: TenantId | None = None, user_id: UserId | None = None
    ) -> GovernanceUnitOfWork:
        return SqlAlchemyIdentityUnitOfWork(
            session_factory, clock, tenant_id=tenant_id, user_id=user_id
        )

    return Container(
        settings=settings,
        engine=engine,
        session_factory=session_factory,
        clock=clock,
        password_hasher=hasher,
        token_issuer=tokens,
        register_owner=RegisterOwner(uow_factory, hasher, clock),
        authenticate=Authenticate(
            uow_factory,
            hasher,
            tokens,
            clock,
            access_token_ttl_seconds=settings.access_token_ttl_seconds,
        ),
        invite_member=InviteMember(
            uow_factory, clock, invitation_ttl_seconds=settings.invitation_ttl_seconds
        ),
        accept_invitation=AcceptInvitation(uow_factory, hasher, clock),
        list_members=ListMembers(uow_factory),
        change_member_role=ChangeMemberRole(uow_factory, clock),
        revoke_membership=RevokeMembership(uow_factory, clock),
        list_audit_entries=ListAuditEntries(governance_uow_factory),
        verify_audit_chain=VerifyAuditChain(governance_uow_factory),
    )
