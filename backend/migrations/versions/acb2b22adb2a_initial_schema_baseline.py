"""initial schema baseline

Revision ID: acb2b22adb2a
Revises:
Create Date: 2026-08-24 21:20:31.463042

Executes data/schema.sql as-is rather than reproducing every CREATE
TABLE by hand - that file stays the one canonical definition of the
baseline schema, and every statement in it is already idempotent
(CREATE TABLE IF NOT EXISTS, etc.), so this is safe to run against a
brand new database (docker-compose's own first-boot bootstrap) or one
that already has schema.sql applied (it's a no-op there, and just
records the migration as the starting point for anything after it).

From here forward, schema changes belong in a new migration
(`alembic revision -m "..."`), not a hand-edit to schema.sql - that file
stops being "the schema" the moment there's a real migration history on
top of it, and only stays around as the fast-path bootstrap for a fresh
database.
"""
from pathlib import Path
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'acb2b22adb2a'
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

SCHEMA_SQL_PATH = Path(__file__).resolve().parents[2] / "data" / "schema.sql"


def upgrade() -> None:
    op.execute(SCHEMA_SQL_PATH.read_text())


def downgrade() -> None:
    # Reverse creation order, CASCADE so a table's own indexes/FKs don't
    # need to be dropped separately or in a precise dependency order.
    for table in [
        "conversation_turns",
        "glossary_terms",
        "verified_queries",
        "document_chunks",
        "enablement_content",
        "account_notes",
        "marketing_touches",
        "activities",
        "consumption_usage",
        "capacity_contracts",
        "deals",
        "partners",
        "account_team",
        "workloads",
        "accounts",
    ]:
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")
