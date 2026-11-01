"""Print the database schema as a Mermaid ER diagram, from the SQLAlchemy metadata.

    uv run python scripts/er_diagram.py > docs/diagrams/er.mmd

Generated rather than drawn, so it can't drift from the models. Only keys and the
columns that explain a table are shown; the full schema is the migrations.
"""

from __future__ import annotations

import sys

import insightlm.contracts.adapters.models
import insightlm.documents.adapters.models
import insightlm.governance.adapters.models
import insightlm.identity.adapters.models
import insightlm.recruiting.adapters.models
import insightlm.retrieval.adapters.models
import insightlm.shared.adapters.db.models  # noqa: F401 -- registers the tables
from insightlm.shared.adapters.db.base import Base

SHOWN_TYPES = ("UUID", "VARCHAR", "TEXT", "INTEGER", "BOOLEAN", "JSONB", "FLOAT", "VECTOR")


def main() -> int:
    lines = ["erDiagram"]
    tables = sorted(Base.metadata.tables.values(), key=lambda t: t.name)
    for table in tables:
        lines.append(f"    {table.name} {{")
        for column in table.columns:
            kind = type(column.type).__name__.upper()
            kind = next((t for t in SHOWN_TYPES if t in kind), kind.lower())
            marks = []
            if column.primary_key:
                marks.append("PK")
            if column.foreign_keys:
                marks.append("FK")
            if not marks and column.name not in {"tenant_id", "status", "stage", "embedding"}:
                continue
            lines.append(f"        {kind} {column.name} {','.join(marks)}".rstrip())
        lines.append("    }")
    for table in tables:
        for fk in table.foreign_keys:
            parent = fk.column.table.name
            lines.append(f'    {parent} ||--o{{ {table.name} : "{fk.parent.name}"')
    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
