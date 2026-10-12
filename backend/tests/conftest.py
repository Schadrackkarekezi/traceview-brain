"""
Tests run against the real local Postgres instance (docker-compose's db
service must be up), using a disposable `widgets` table created and
dropped per test - not a mock. This is what lets test_query_engine.py
exercise the actual traceview_readonly role's actual Postgres-enforced
privileges, not a simulation of them.
"""

import psycopg
import pytest

import app.database as database


@pytest.fixture(autouse=True)
def no_live_hydradb(monkeypatch):
    """
    The one external service these tests must never reach. Postgres here
    is a local, disposable dev database; HydraDB is a real account, and
    a developer with HYDRA_DB_API_KEY in backend/.env would otherwise have
    every upload test copy its throwaway note into it - the tests delete
    those notes from Postgres afterwards, but nothing deleted them from
    HydraDB. Confirmed the hard way: 12 stray test notes on one account
    after four runs of this suite.

    Forcing the pgvector path is also what the existing retrieval and
    upload tests are actually about. test_hydra_store.py opts back in,
    with a fake client, through its own fixture.
    """
    monkeypatch.setenv("RETRIEVAL_BACKEND", "pgvector")


@pytest.fixture
def test_db():
    with psycopg.connect(database.DATABASE_URL) as conn:
        conn.execute("DROP TABLE IF EXISTS widgets")
        conn.execute("CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT, price INTEGER)")
        conn.execute(
            "INSERT INTO widgets (id, name, price) VALUES (1, 'gadget', 100), (2, 'gizmo', 250)"
        )
        conn.commit()

    yield

    with psycopg.connect(database.DATABASE_URL) as conn:
        conn.execute("DROP TABLE IF EXISTS widgets")
        conn.commit()
