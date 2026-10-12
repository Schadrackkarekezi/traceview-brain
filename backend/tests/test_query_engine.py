"""
Tests for the safety boundary in query_engine.run_select().

These are the most important tests in the project - this is the function
that decides whether AI-generated (or hand-typed) SQL is allowed to touch
the database at all. The actual enforcement is Postgres's own privilege
system (the traceview_readonly role has SELECT and nothing else - see
schema.sql), so these tests are exercising a real database role, not a
mock of one.
"""

import pytest

from app.query_engine import run_select, QueryError


def test_select_is_allowed(test_db):
    columns, rows, elapsed_ms, truncated = run_select("SELECT * FROM widgets ORDER BY id")
    assert columns == ["id", "name", "price"]
    assert rows == [[1, "gadget", 100], [2, "gizmo", 250]]
    assert truncated is False


def test_legit_cte_is_allowed(test_db):
    columns, rows, _, _ = run_select(
        "WITH expensive AS (SELECT * FROM widgets WHERE price > 150) SELECT COUNT(*) FROM expensive"
    )
    assert rows == [[1]]


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE widgets",
        "DELETE FROM widgets",
        "UPDATE widgets SET price = 0",
        "INSERT INTO widgets (id, name, price) VALUES (3, 'x', 1)",
        "ALTER TABLE widgets ADD COLUMN hacked TEXT",
        "TRUNCATE widgets",
        "COPY widgets TO '/tmp/traceview_exfil.csv'",
        "SET SESSION AUTHORIZATION traceview",  # role-escalation attempt
        "WITH x AS (SELECT 1) DELETE FROM widgets",  # disguised write via CTE
    ],
)
def test_writes_and_dangerous_statements_are_rejected(test_db, sql):
    with pytest.raises(QueryError):
        run_select(sql)

    # and the data must be untouched afterward, not just "an error happened"
    columns, rows, _, _ = run_select("SELECT COUNT(*) FROM widgets")
    assert rows == [[2]]


def test_bad_sql_raises_clean_error_not_crash(test_db):
    with pytest.raises(QueryError):
        run_select("SELECT * FROM no_such_table")


def test_empty_query_rejected(test_db):
    with pytest.raises(QueryError):
        run_select("   ")


def test_stacked_statements_rejected(test_db):
    with pytest.raises(QueryError):
        run_select("SELECT * FROM widgets; DROP TABLE widgets")


def test_row_cap_truncates(test_db, monkeypatch):
    import app.query_engine as query_engine

    monkeypatch.setattr(query_engine, "MAX_ROWS", 1)
    columns, rows, _, truncated = run_select("SELECT * FROM widgets")
    assert len(rows) == 1
    assert truncated is True


def test_query_timeout(test_db, monkeypatch):
    import app.query_engine as query_engine

    monkeypatch.setattr(query_engine, "TIMEOUT_SECONDS", 0.01)
    with pytest.raises(QueryError, match="timed out"):
        run_select(
            "WITH RECURSIVE cnt(x) AS "
            "(SELECT 1 UNION ALL SELECT x + 1 FROM cnt) "
            "SELECT count(*) FROM cnt"
        )
