"""Platform foundation: tenancy, identity, audit chain, prompt registry, outbox.

Revision ID: 0001_platform_foundation
Revises:
Create Date: 2026-09-18
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from insightlm.settings import get_settings

revision: str = "0001_platform_foundation"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Tables carrying tenant data. Each gets RLS keyed on app.tenant_id.
TENANT_SCOPED: tuple[tuple[str, str], ...] = (
    ("tenants", "id"),
    ("memberships", "tenant_id"),
    ("invitations", "tenant_id"),
    ("audit_entries", "tenant_id"),
)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")

    # ------------------------------------------------------------------ identity
    op.create_table(
        "tenants",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("slug", sa.String(length=40), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_tenants"),
        sa.UniqueConstraint("slug", name="uq_tenants_slug"),
    )

    # Not tenant-scoped: a user is a global identity and holds no tenant data. The
    # login path must resolve an email before any tenant context can exist. Everything
    # binding a user to a tenant lives on `memberships`, which IS protected. ADR-0016.
    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_users"),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )

    op.create_table(
        "memberships",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_memberships_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_memberships_user_id_users", ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_memberships"),
        sa.UniqueConstraint("tenant_id", "user_id", name="uq_memberships_tenant_user"),
    )
    op.create_index("ix_memberships_user_id", "memberships", ["user_id"])

    op.create_table(
        "invitations",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=False),
        sa.Column("role", sa.String(length=32), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("invited_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["tenant_id"],
            ["tenants.id"],
            name="fk_invitations_tenant_id_tenants",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by"],
            ["users.id"],
            name="fk_invitations_invited_by_users",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["accepted_by"],
            ["users.id"],
            name="fk_invitations_accepted_by_users",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_invitations"),
        sa.UniqueConstraint("tenant_id", "token_hash", name="uq_invitations_tenant_token_hash"),
    )
    op.create_index("ix_invitations_tenant_email", "invitations", ["tenant_id", "email"])

    # ---------------------------------------------------------------- governance
    op.create_table(
        "audit_entries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        # No FK to tenants/users by design -- see governance/adapters/models.py. The
        # audit trail must survive erasure of whatever it describes.
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("actor_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("subject_type", sa.String(length=64), nullable=False),
        sa.Column("subject_id", sa.String(length=128), nullable=True),
        sa.Column(
            "payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=128), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_audit_entries"),
        sa.UniqueConstraint("tenant_id", "sequence", name="uq_audit_entries_tenant_sequence"),
        sa.UniqueConstraint("tenant_id", "entry_hash", name="uq_audit_entries_tenant_entry_hash"),
    )
    op.create_index(
        "ix_audit_entries_tenant_occurred", "audit_entries", ["tenant_id", "occurred_at"]
    )
    op.create_index(
        "ix_audit_entries_subject", "audit_entries", ["tenant_id", "subject_type", "subject_id"]
    )

    op.create_table(
        "prompt_templates",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), server_default="", nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_prompt_templates"),
        sa.UniqueConstraint("name", name="uq_prompt_templates_name"),
    )

    op.create_table(
        "prompt_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("template_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["prompt_templates.id"],
            name="fk_prompt_versions_template_id_prompt_templates",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_prompt_versions"),
        sa.UniqueConstraint("template_id", "version", name="uq_prompt_versions_template_version"),
        sa.UniqueConstraint("content_hash", name="uq_prompt_versions_content_hash"),
    )

    op.create_table(
        "model_versions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("purpose", sa.String(length=64), nullable=False),
        sa.Column(
            "parameters", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id", name="pk_model_versions"),
        sa.UniqueConstraint("provider", "model_id", "purpose", name="uq_model_versions_identity"),
    )

    # -------------------------------------------------------------------- outbox
    op.create_table(
        "outbox_events",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column(
            "payload", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column(
            "attributes", postgresql.JSONB(), server_default=sa.text("'{}'::jsonb"), nullable=False
        ),
        sa.Column("correlation_id", sa.String(length=64), nullable=True),
        sa.Column(
            "occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("last_error", sa.String(length=1024), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_events"),
    )
    op.create_index("ix_outbox_events_tenant_id", "outbox_events", ["tenant_id"])
    op.create_index(
        "ix_outbox_events_unprocessed",
        "outbox_events",
        ["occurred_at"],
        postgresql_where=sa.text("processed_at IS NULL"),
    )

    _install_tenant_context()
    _install_row_level_security()
    _install_audit_append_only_guard()
    _install_application_role()


def _install_tenant_context() -> None:
    """The function every policy consults.

    Returns NULL when `app.tenant_id` is unset, so every policy comparison yields NULL
    and denies. The system fails closed -- a query that forgets to establish tenant
    context returns nothing rather than everything.
    """
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_tenant_id() RETURNS uuid
        LANGUAGE sql STABLE
        AS $$
            SELECT NULLIF(current_setting('app.tenant_id', true), '')::uuid
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION current_actor_id() RETURNS uuid
        LANGUAGE sql STABLE
        AS $$
            SELECT NULLIF(current_setting('app.user_id', true), '')::uuid
        $$;
        """
    )
    # SECURITY DEFINER matters here. A policy on `tenants` that consults `memberships`
    # would otherwise trigger `memberships`' own policy, which consults... and Postgres
    # raises on the recursion. Running the lookup as the function owner reads the table
    # without RLS, which is the documented way out.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION user_is_member_of(target_tenant uuid) RETURNS boolean
        LANGUAGE sql STABLE SECURITY DEFINER
        AS $$
            SELECT EXISTS (
                SELECT 1 FROM memberships
                WHERE memberships.tenant_id = target_tenant
                  AND memberships.user_id = current_actor_id()
                  AND memberships.revoked_at IS NULL
            )
        $$;
        """
    )


def _install_application_role() -> None:
    """Create the restricted role the application actually connects as.

    This is the step that makes every policy above real. PostgreSQL grants SUPERUSER
    roles an unconditional bypass of row-level security -- `FORCE ROW LEVEL SECURITY`
    constrains the table *owner*, but nothing constrains a superuser. An application
    connecting as the default `postgres` user therefore has no tenant isolation at all,
    however many policies exist. That failure is silent, which is what makes it
    dangerous: every policy still shows up in the catalog, and every query still returns
    everything.

    Creating the role here rather than in a compose init script means `alembic upgrade
    head` produces a correctly locked-down database everywhere -- local, CI and
    testcontainers alike -- instead of only where someone remembered to run the script.

    The role is also denied UPDATE and DELETE on `audit_entries`. That is now a real
    second layer alongside the trigger: the app cannot rewrite history even if the
    trigger is dropped, because it lacks the privilege to try.
    """
    settings = get_settings()
    connection = op.get_bind()

    # Pass the password as a bound parameter and let PostgreSQL quote it via format(%L).
    # CREATE ROLE cannot be parameterised directly, and string-building it would be an
    # injection waiting to happen.
    connection.execute(
        sa.text("SELECT set_config('insightlm.bootstrap_role', :role, false)"),
        {"role": settings.database_app_role},
    )
    connection.execute(
        sa.text("SELECT set_config('insightlm.bootstrap_password', :pw, false)"),
        {"pw": settings.database_app_password.get_secret_value()},
    )
    connection.execute(
        sa.text(
            """
            DO $$
            DECLARE
                role_name text := current_setting('insightlm.bootstrap_role');
                role_pw   text := current_setting('insightlm.bootstrap_password');
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
                    EXECUTE format(
                        'CREATE ROLE %I LOGIN PASSWORD %L '
                        'NOSUPERUSER NOBYPASSRLS NOCREATEDB NOCREATEROLE NOINHERIT',
                        role_name, role_pw
                    );
                ELSE
                    EXECUTE format('ALTER ROLE %I PASSWORD %L', role_name, role_pw);
                END IF;
            END $$;
            """
        )
    )

    role = settings.database_app_role
    op.execute(f'GRANT USAGE ON SCHEMA public TO "{role}"')
    op.execute(f'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "{role}"')
    op.execute(f'GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO "{role}"')
    # Append-only, enforced by privilege as well as by trigger.
    op.execute(f'REVOKE UPDATE, DELETE, TRUNCATE ON audit_entries FROM "{role}"')


def _install_row_level_security() -> None:
    # Two flows legitimately have a user but no tenant: logging in, and listing the
    # workspaces you belong to. Rather than carving out an exemption for them, these
    # two tables state the actual rule -- you may always see your own memberships, and
    # the tenants they are in. Writes stay strictly tenant-scoped either way.
    self_service = {
        "memberships": "user_id = current_actor_id()",
        "tenants": "user_is_member_of(id)",
    }

    for table, column in TENANT_SCOPED:
        # FORCE constrains the table OWNER, which matters if the app ever connects as
        # the owner. It does NOT constrain a superuser -- see _install_application_role.
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")

        read_rule = f"{column} = current_tenant_id()"
        if table in self_service:
            read_rule = f"({read_rule} OR {self_service[table]})"

        op.execute(
            f"""
            CREATE POLICY {table}_tenant_isolation ON {table}
            FOR ALL
            USING ({read_rule})
            WITH CHECK ({column} = current_tenant_id())
            """
        )

    # The relay is cross-tenant by definition, so it declares itself instead. See the
    # module docstring of shared/adapters/db/relay.py for what this does and does not
    # buy, and ADR-0016 for the separate-role migration path.
    op.execute("ALTER TABLE outbox_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE outbox_events FORCE ROW LEVEL SECURITY")
    op.execute(
        """
        CREATE POLICY outbox_events_tenant_isolation ON outbox_events
        FOR ALL
        USING (
            tenant_id = current_tenant_id()
            OR current_setting('app.relay', true) = 'on'
        )
        WITH CHECK (
            tenant_id = current_tenant_id()
            OR current_setting('app.relay', true) = 'on'
        )
        """
    )


def _install_audit_append_only_guard() -> None:
    """Append-only, enforced by the database.

    A trigger rather than a GRANT, because a GRANT does not constrain the table owner
    and this deployment connects as the owner. The trigger fires for every role,
    superuser included.

    This is the first of two layers. The hash chain is the second: if the trigger is
    ever disabled -- as `tests/security/test_audit_tamper_evidence.py` does
    deliberately -- verification still detects the mutation.
    """
    op.execute(
        """
        CREATE OR REPLACE FUNCTION reject_audit_mutation() RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            RAISE EXCEPTION
                'audit_entries is append-only; % is not permitted', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER audit_entries_append_only
        BEFORE UPDATE OR DELETE ON audit_entries
        FOR EACH ROW EXECUTE FUNCTION reject_audit_mutation();
        """
    )


def downgrade() -> None:
    role = get_settings().database_app_role
    op.execute(f'REVOKE ALL ON ALL TABLES IN SCHEMA public FROM "{role}"')
    op.execute(f'REVOKE ALL ON SCHEMA public FROM "{role}"')

    op.execute("DROP TRIGGER IF EXISTS audit_entries_append_only ON audit_entries")
    op.execute("DROP FUNCTION IF EXISTS reject_audit_mutation()")

    op.execute("DROP POLICY IF EXISTS outbox_events_tenant_isolation ON outbox_events")
    for table, _ in TENANT_SCOPED:
        op.execute(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")

    op.execute("DROP FUNCTION IF EXISTS user_is_member_of(uuid)")
    op.execute("DROP FUNCTION IF EXISTS current_actor_id()")
    op.execute("DROP FUNCTION IF EXISTS current_tenant_id()")

    op.drop_table("outbox_events")
    op.drop_table("model_versions")
    op.drop_table("prompt_versions")
    op.drop_table("prompt_templates")
    op.drop_table("audit_entries")
    op.drop_table("invitations")
    op.drop_table("memberships")
    op.drop_table("users")
    op.drop_table("tenants")
