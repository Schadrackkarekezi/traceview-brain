"""
Isolation tests for unstructured retrieval - the account_id boundary in
retrieval.py is a real correctness/security property, not a nice-to-have:
a leak here means one account's private notes surfacing in another
account's answer. These run against the real seeded data and the real
pgvector index, not a mock, the same way the SQL safety tests exercise
the real traceview_readonly role.
"""

import app.retrieval as retrieval


def _account_ids_with_notes(limit=2):
    from app.database import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT account_id FROM account_notes ORDER BY account_id LIMIT %s", (limit,))
            return [row[0] for row in cur.fetchall()]


def test_account_scoped_query_never_returns_another_accounts_note():
    ids = _account_ids_with_notes(2)
    assert len(ids) >= 2, "need at least two accounts with notes seeded to test isolation"
    a, b = ids[0], ids[1]

    q = "Why is consumption declining and who is the renewal risk owner?"
    results_a = retrieval.retrieve_unstructured(q, account_id=a, top_k=5)
    results_b = retrieval.retrieve_unstructured(q, account_id=b, top_k=5)

    note_accounts_a = {r["account_id"] for r in results_a if r["source_type"] == "account_note"}
    note_accounts_b = {r["account_id"] for r in results_b if r["source_type"] == "account_note"}

    assert note_accounts_a <= {a}
    assert note_accounts_b <= {b}


def test_global_scope_never_returns_account_notes():
    results = retrieval.retrieve_unstructured("How should I handle a pricing objection?", account_id=None, top_k=5)
    assert all(r["source_type"] != "account_note" for r in results)
    assert all(r["account_id"] is None for r in results)


def test_retrieve_returns_results_for_a_reasonable_question():
    results = retrieval.retrieve_unstructured("How do capacity contracts and true-ups work?", account_id=None, top_k=3)
    assert len(results) > 0
    assert results[0]["score"] > 0.5
