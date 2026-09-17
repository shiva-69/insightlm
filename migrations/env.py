"""Alembic environment.

Every module's persistence models are imported here so ``Base.metadata`` is complete.
**Every module with persistence must be listed below.** A missing import does not
fail loudly: autogenerate simply proposes dropping that module's tables, and a
migration generated from that diff would delete real data. This is why CI runs
``alembic check`` against a freshly migrated database -- the gate caught exactly this
omission when the Phase 1 tables were added.
"""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Imported for their side effect of registering tables on Base.metadata.
from insightlm.documents.adapters import models as _documents_models  # noqa: F401
from insightlm.governance.adapters import models as _governance_models  # noqa: F401
from insightlm.identity.adapters import models as _identity_models  # noqa: F401
from insightlm.retrieval.adapters import models as _retrieval_models  # noqa: F401
from insightlm.settings import get_settings
from insightlm.shared.adapters.db import models as _shared_models  # noqa: F401
from insightlm.shared.adapters.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().sync_database_url)
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
