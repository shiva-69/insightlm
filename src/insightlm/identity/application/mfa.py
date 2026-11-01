"""Turning two-factor authentication on and off.

Enrolment is two steps on purpose. ``StartMfaEnrolment`` makes a secret and returns it
once, for the person to add to their authenticator app; nothing about sign-in changes
yet. ``ConfirmMfaEnrolment`` turns it on only after a code from that app verifies, so a
mistyped secret can never lock anyone out. Turning it off takes a current code too: a
stolen session alone cannot remove the second factor.
"""

from __future__ import annotations

from dataclasses import dataclass

from insightlm.identity.domain import totp
from insightlm.identity.domain.audit_actions import IdentityAudit
from insightlm.identity.ports.uow import IdentityUnitOfWorkFactory
from insightlm.shared.domain.errors import AuthenticationError, NotFoundError
from insightlm.shared.domain.policy import Actor
from insightlm.shared.ports.clock import Clock
from insightlm.shared.ports.secrets import SecretBox


@dataclass(frozen=True, slots=True)
class Enrolment:
    secret: str
    """Shown once. Never stored in the clear and never returned again."""
    provisioning_uri: str


class StartMfaEnrolment:
    def __init__(self, uow_factory: IdentityUnitOfWorkFactory, box: SecretBox) -> None:
        self._uow_factory = uow_factory
        self._box = box

    async def execute(self, actor: Actor) -> Enrolment:
        async with self._uow_factory(user_id=actor.user_id) as uow:
            user = await uow.users.get(actor.user_id)
            if user is None:
                raise NotFoundError("user not found")
            secret = totp.new_secret()
            await uow.users.update(user.with_pending_mfa(self._box.seal(secret)))
            await uow.commit()
        return Enrolment(secret, totp.provisioning_uri(secret, account=user.email))


class ConfirmMfaEnrolment:
    def __init__(
        self, uow_factory: IdentityUnitOfWorkFactory, box: SecretBox, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._box = box
        self._clock = clock

    async def execute(self, actor: Actor, code: str) -> None:
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            user = await uow.users.get(actor.user_id)
            if user is None:
                raise NotFoundError("user not found")
            if user.mfa_sealed_secret is None:
                raise AuthenticationError("start two-factor enrolment first")
            step = totp.verify(self._box.open(user.mfa_sealed_secret), code, self._clock.now())
            if step is None:
                raise AuthenticationError("that code did not match; check the time on your phone")
            await uow.users.update(user.with_mfa_confirmed(step))
            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=IdentityAudit.MFA_ENABLED,
                subject_type="user",
                subject_id=str(actor.user_id),
                payload={},
            )
            await uow.commit()


class DisableMfa:
    def __init__(
        self, uow_factory: IdentityUnitOfWorkFactory, box: SecretBox, clock: Clock
    ) -> None:
        self._uow_factory = uow_factory
        self._box = box
        self._clock = clock

    async def execute(self, actor: Actor, code: str) -> None:
        async with self._uow_factory(tenant_id=actor.tenant_id, user_id=actor.user_id) as uow:
            user = await uow.users.get(actor.user_id)
            if user is None or not user.mfa_enabled or user.mfa_sealed_secret is None:
                raise AuthenticationError("two-factor authentication is not on")
            step = totp.verify(self._box.open(user.mfa_sealed_secret), code, self._clock.now())
            if step is None:
                raise AuthenticationError("that code did not match")
            await uow.users.update(user.accepting_step(step).without_mfa())
            await uow.audit.record(
                tenant_id=actor.tenant_id,
                actor_id=actor.user_id,
                action=IdentityAudit.MFA_DISABLED,
                subject_type="user",
                subject_id=str(actor.user_id),
                payload={},
            )
            await uow.commit()


class MfaStatus:
    def __init__(self, uow_factory: IdentityUnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def execute(self, actor: Actor) -> bool:
        async with self._uow_factory(user_id=actor.user_id) as uow:
            user = await uow.users.get(actor.user_id)
        return bool(user and user.mfa_enabled)
