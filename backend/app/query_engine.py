"""
Executes read-only SQL against the traceview_readonly Postgres role.

The safety boundary here is the database itself, not this file: the
connecting role has been granted SELECT and nothing else, at the Postgres
privilege level (see schema.sql). A write statement doesn't get rejected
by this code recognizing it as dangerous - it gets rejected by Postgres
because the role is structurally incapable of writing, the same way a
read replica is. This is the direct equivalent of the original SQLite
implementation's set_authorizer hook, ported to Postgres's idiomatic
mechanism: SQLite has no user model, so a compile-time callback was the
right SQLite-native answer; Postgres has a real multi-user privilege
system, so a dedicated role is the right Postgres-native answer.

Three defenses stack here, in order:
  1. Reject multiple statements at the application layer (belt and
     suspenders - don't rely solely on driver behavior).
  2. Wrap every query in a subquery + LIMIT, so no result set can be
     larger than MAX_ROWS regardless of what was asked.
  3. A Postgres statement_timeout, set per-connection, so no query can
     run longer than TIMEOUT_SECONDS regardless of what it's doing.

Even if all three were somehow bypassed, step zero - the role's actual
privileges - is still there underneath, enforced by the database engine
itself rather than by this file being correct.
"""

import os
import time

import psycopg
from dotenv import load_dotenv

from app.database import READONLY_DATABASE_URL

load_dotenv()

MAX_ROWS = int(os.environ.get("QUERY_MAX_ROWS", 500))
TIMEOUT_SECONDS = float(os.environ.get("QUERY_TIMEOUT_SECONDS", 5))


class QueryError(Exception):
    pass


def run_select(sql: str):
    stripped = sql.strip().rstrip(";").strip()

    if not stripped:
        raise QueryError("Empty query.")
    if ";" in stripped:
        raise QueryError("Multiple statements are not allowed - submit one query at a time.")

    wrapped = f"SELECT * FROM (\n{stripped}\n) AS traceview_subq LIMIT {MAX_ROWS + 1}"

    t0 = time.perf_counter()
    try:
        with psycopg.connect(
            READONLY_DATABASE_URL,
            options=f"-c statement_timeout={int(TIMEOUT_SECONDS * 1000)}",
        ) as conn:
            with conn.cursor() as cur:
                cur.execute(wrapped)
                columns = [desc.name for desc in cur.description]
                rows = [list(row) for row in cur.fetchall()]
    except psycopg.errors.QueryCanceled as e:
        raise QueryError(f"Query timed out after {TIMEOUT_SECONDS}s.") from e
    except psycopg.errors.InsufficientPrivilege as e:
        raise QueryError("Query rejected - only read access is permitted.") from e
    except psycopg.Error as e:
        raise QueryError(str(e).strip()) from e

    elapsed_ms = round((time.perf_counter() - t0) * 1000, 2)

    truncated = len(rows) > MAX_ROWS
    if truncated:
        rows = rows[:MAX_ROWS]

    return columns, rows, elapsed_ms, truncated
