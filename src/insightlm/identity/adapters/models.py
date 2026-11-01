"""Persistence models for the identity module."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from insightlm.shared.adapters.db.base import Base


class TenantRow(Base):
    __tablename__ = "tenants"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    slug: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserRow(Base):
    """Global identity.

    Deliberately *not* under row-level security: a user row holds no tenant data, and
    the login path must resolve an email before any tenant context exists. Everything
    that binds a user to a tenant lives on ``memberships``, which is protected.
    See ADR-0016.
    """

    __tablename__ = "users"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    display_name: Mapped[str] = mapped_column(String(200), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    mfa_sealed_secret: Mapped[str | None] = mapped_column(Text, nullable=True)
    mfa_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    mfa_last_step: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default="0"
    )


class MembershipRow(Base):
    __tablename__ = "memberships"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    # See "flush ordering" at the foot of this module.
    tenant: Mapped[TenantRow] = relationship(lazy="raise")
    user: Mapped[UserRow] = relationship(lazy="raise")

    __table_args__ = (
        # One membership per user per tenant. Re-inviting a revoked member reactivates
        # the existing row rather than creating a second one.
        UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
        Index("ix_memberships_user_id", "user_id"),
    )


class InvitationRow(Base):
    __tablename__ = "invitations"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True)
    tenant_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("tenants.id", ondelete="CASCADE"), nullable=False
    )
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    token_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    invited_by: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accepted_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    tenant: Mapped[TenantRow] = relationship(lazy="raise")
    inviter: Mapped[UserRow] = relationship(lazy="raise", foreign_keys=[invited_by])
    acceptor: Mapped[UserRow | None] = relationship(lazy="raise", foreign_keys=[accepted_by])

    __table_args__ = (
        UniqueConstraint("tenant_id", "token_hash", name="uq_invitations_tenant_token_hash"),
        Index("ix_invitations_tenant_email", "tenant_id", "email"),
    )


# --- flush ordering -----------------------------------------------------------
# These relationships exist for exactly one reason: SQLAlchemy orders the INSERTs in a
# flush by the dependency graph between *mappers*, and it builds that graph from
# `relationship()` -- not from ForeignKey columns. Without them the ORM may insert an
# audit entry before the tenant it references, which fails on the foreign key.
#
# `lazy="raise"` is the important half. This codebase maps rows to domain objects
# explicitly inside repositories; lazy loading behind a repository port is how you get
# N+1 queries and detached-instance errors. Declaring the dependency while making the
# attribute unusable gives us the ordering and none of the footgun -- touching one of
# these raises instead of quietly emitting SQL.
