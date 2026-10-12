"""
Admin-role Postgres connection - used for schema/FK introspection and by
seed_db.py. This is NOT the safety boundary for user- or agent-generated
SQL; that's query_engine.py, which connects as the separate, privilege-
restricted traceview_readonly role instead.
"""

import os
import re
from contextlib import contextmanager

import psycopg
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://traceview:traceview_dev_only@localhost:5432/traceview"
)

# The read-only role's connection string - query_engine.py, retrieval.py,
# and verified_queries.py all connect as this same least-privilege role
# for their SELECT-only workloads, so it's defined once here rather than
# copy-pasted three times.
READONLY_DATABASE_URL = os.environ.get(
    "DATABASE_URL_READONLY",
    "postgresql://traceview_readonly:traceview_readonly_dev_only@localhost:5432/traceview",
)


@contextmanager
def get_connection():
    conn = psycopg.connect(DATABASE_URL)
    try:
        yield conn
    finally:
        conn.close()


def fetch_schema(conn) -> list:
    """
    One entry per table: {"name": ..., "columns": [{"name": ..., "type": ...}, ...]}.
    Read from Postgres's own information_schema, ordered by column position,
    so it can't drift from what's actually in the database.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT table_name, column_name, data_type
            FROM information_schema.columns
            WHERE table_schema = 'public'
            ORDER BY table_name, ordinal_position
            """
        )
        rows = cur.fetchall()

    tables = {}
    for table_name, column_name, data_type in rows:
        tables.setdefault(table_name, []).append({"name": column_name, "type": data_type})

    return [{"name": name, "columns": cols} for name, cols in tables.items()]


def fetch_foreign_keys(conn) -> list:
    """
    Real FK constraints from Postgres's own catalog - knowledge_graph.py
    builds its edges from this, so the graph can't drift from the actual
    schema.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT
                tc.table_name AS from_table,
                kcu.column_name AS from_column,
                ccu.table_name AS to_table,
                ccu.column_name AS to_column
            FROM information_schema.table_constraints tc
            JOIN information_schema.key_column_usage kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage ccu
                ON tc.constraint_name = ccu.constraint_name
                AND tc.table_schema = ccu.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
            ORDER BY tc.table_name
            """
        )
        rows = cur.fetchall()

    return [
        {"from_table": from_table, "from_column": from_column, "to_table": to_table, "to_column": to_column}
        for from_table, from_column, to_table, to_column in rows
    ]


def fetch_check_constraint_values(conn) -> dict:
    """
    {"table.column": ["value1", "value2", ...]} for every single-column
    CHECK (... IN (...)) constraint in the schema - read from Postgres's
    own pg_get_constraintdef, not hand-maintained, so it can't drift from
    schema.sql.

    Without this, the agent has no way to know which literal strings are
    actually valid for a column like activity_type or stage - it can only
    guess a plausible-sounding one (confirmed: it once wrote
    activity_type = 'POC', which matches zero rows because the real value
    is 'poc_kickoff' - a silent wrong answer, not an error, since the query
    is syntactically fine and just returns nothing).
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT t.relname AS table_name, a.attname AS column_name, pg_get_constraintdef(con.oid) AS def
            FROM pg_constraint con
            JOIN pg_class t ON con.conrelid = t.oid
            JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(con.conkey)
            WHERE con.contype = 'c' AND t.relnamespace = 'public'::regnamespace
            """
        )
        rows = cur.fetchall()

    values_by_column = {}
    for table_name, column_name, definition in rows:
        literals = re.findall(r"'([^']*)'", definition)
        if literals:
            values_by_column[f"{table_name}.{column_name}"] = literals
    return values_by_column
