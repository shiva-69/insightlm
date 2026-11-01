"""Login."""

from __future__ import annotations

import uuid

from insightlm.identity.application.commands import AuthenticateCommand, AuthenticateResult
from insightlm.identity.domain import totp
from insightlm.identity.domain.audit_actions import IdentityAudit
from insightlm.identity.domain.entities import normalise_email
from insightlm.identity.ports.uow import IdentityUnitOfWorkFactory
from insightlm.shared.domain.errors import AuthenticationError, ConflictError
from insightlm.shared.ports.clock import Clock
from insightlm.shared.ports.password import PasswordHasher
from insightlm.shared.ports.secrets import SecretBox
from insightlm.shared.ports.tokens import TokenClaims, TokenIssuer


class Authenticate:
    """Verifies credentials and issues a tenant-scoped access token.

    Every failure path returns the same message and does the same amount of work: an
    unknown email still runs a password verification against a dummy hash, so response
    time does not reveal whether an account exists.
    """

    _GENERIC_FAILURE = "invalid email or password"

    def __init__(
        self,
        uow_factory: IdentityUnitOfWorkFactory,
        hasher: PasswordHasher,
        tokens: TokenIssuer,
        clock: Clock,
        *,
        access_token_ttl_seconds: int,
        box: SecretBox | None = None,
    ) -> None:
        self._box = box
        self._uow_factory = uow_factory
        self._hasher = hasher
        self._tokens = tokens
        self._clock = clock
        self._ttl = access_token_ttl_seconds
        self._dummy_hash = hasher.hash(uuid.uuid4().hex)

    async def execute(self, command: AuthenticateCommand) -> AuthenticateResult:
        email = normalise_email(command.email)

        # Step one: resolve and verify the credential. `users` carries no tenant data
        # and is not under row-level security, so this needs no context at all.
        async with self._uow_factory() as uow:
            user = await uow.users.get_by_email(email)

            if user is None:
                self._hasher.verify(command.password, self._dummy_hash)
                raise AuthenticationError(self._GENERIC_FAILURE)
            if not self._hasher.verify(command.password, user.password_hash):
                raise AuthenticationError(self._GENERIC_FAILURE)
            if not user.is_active:
                raise AuthenticationError(self._GENERIC_FAILURE)
            if user.mfa_enabled:
                # Asked for only after the password is right, so it reveals nothing to
                # someone guessing passwords. A code is accepted once: its step is
                # recorded, and the same or an older step is refused.
                if not command.mfa_code:
                    raise AuthenticationError(
                        "enter the code from your authenticator app",
                        detail={"mfa_required": True},
                    )
                if self._box is None or user.mfa_sealed_secret is None:
                    raise AuthenticationError(self._GENERIC_FAILURE)
                step = totp.verify(
                    self._box.open(user.mfa_sealed_secret), command.mfa_code, self._clock.now()
                )
                if step is None:
                    raise AuthenticationError(
                        "that code did not match", detail={"mfa_required": True}
                    )
                try:
                    user = user.accepting_step(step)
                except ConflictError as error:
                    raise AuthenticationError(
                        "that code has already been used; wait for the next one",
                        detail={"mfa_required": True},
                    ) from error
                await uow.users.update(user)
                await uow.commit()

        # Step two: work out which workspace this login is for. Scoped to the *user*,
        # not a tenant -- which is precisely the case the self-service RLS policies
        # exist for. We do not know the tenant yet; that is what we are resolving.
        async with self._uow_factory(user_id=user.id) as scoped:
            memberships = await scoped.memberships.list_for_user(user.id)
            if not memberships:
                raise AuthenticationError(self._GENERIC_FAILURE)

            if command.tenant_slug is not None:
                wanted = command.tenant_slug.strip().lower()
                membership = None
                for candidate in memberships:
                    tenant = await scoped.tenants.get(candidate.tenant_id)
                    if tenant is not None and tenant.slug == wanted:
                        membership = candidate
                        break
                if membership is None:
                    raise AuthenticationError(self._GENERIC_FAILURE)
            elif len(memberships) > 1:
                raise AuthenticationError(
                    "this account belongs to several workspaces; specify tenant_slug"
                )
            else:
                membership = memberships[0]

        token = self._tokens.issue_access_token(
            TokenClaims(
                user_id=user.id,
                tenant_id=membership.tenant_id,
                role=membership.role.value,
                jti=uuid.uuid4().hex,
            )
        )

        # Audited in its own transaction, scoped to the tenant just resolved. Keeping
        # it out of the transaction above means an audit failure can never hand back a
        # token that was not recorded -- the order matters more than the atomicity.
        async with self._uow_factory(tenant_id=membership.tenant_id) as audit_uow:
            await audit_uow.audit.record(
                tenant_id=membership.tenant_id,
                actor_id=user.id,
                action=IdentityAudit.USER_AUTHENTICATED,
                subject_type="user",
                subject_id=str(user.id),
                payload={"role": membership.role.value},
            )
            await audit_uow.commit()

        return AuthenticateResult(
            access_token=token, expires_in=self._ttl, user=user, membership=membership
        )
