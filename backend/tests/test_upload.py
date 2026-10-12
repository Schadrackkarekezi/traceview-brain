"""
The upload isolation guarantee is a real correctness/security property,
same category as the SQL safety tests - a leak here means one account's
uploaded note surfacing in another account's (or a global) answer. These
run against the real database and the real pgvector index, uploading and
then immediately attacking the isolation boundary, the same discipline
used for the SQL disguised-write live-attack test.
"""

import pytest

import app.retrieval as retrieval
import app.upload as upload
from app.database import get_connection


@pytest.fixture
def uploaded_notes():
    """Yields a list to append note_ids to; deletes them (and their
    chunks) after the test, so isolation tests don't leave permanent
    clutter in real account_notes/document_chunks."""
    note_ids = []
    yield note_ids
    if note_ids:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.executemany(
                    "DELETE FROM document_chunks WHERE source_type = 'account_note' AND source_id = %s",
                    [(i,) for i in note_ids],
                )
                cur.executemany("DELETE FROM account_notes WHERE note_id = %s", [(i,) for i in note_ids])
            conn.commit()


def _first_account_id():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT account_id FROM accounts ORDER BY account_id LIMIT 1")
            return cur.fetchone()[0]


def test_uploaded_note_never_leaks_to_a_different_account(uploaded_notes):
    account_id = _first_account_id()
    marker = "ISOLATION_TEST_MARKER_7f3a"
    result = upload.upload_account_note(account_id, f"{marker}: a distinctive, made-up fact.", "CSM")
    uploaded_notes.append(result["id"])

    other_id = account_id + 1  # any other account works for this check
    other_results = retrieval.retrieve_unstructured(
        "any risks or notable facts about this account", account_id=other_id, top_k=10
    )
    assert not any(marker in r["text"] for r in other_results)


def test_uploaded_note_never_leaks_to_global_search(uploaded_notes):
    account_id = _first_account_id()
    marker = "ISOLATION_TEST_MARKER_9c1d"
    result = upload.upload_account_note(account_id, f"{marker}: another distinctive fact.", "CSM")
    uploaded_notes.append(result["id"])

    global_results = retrieval.retrieve_unstructured("any risks or notable facts", account_id=None, top_k=10)
    assert not any(marker in r["text"] for r in global_results)


def test_uploaded_note_is_retrievable_for_its_own_account(uploaded_notes):
    account_id = _first_account_id()
    marker = "ISOLATION_TEST_MARKER_be2f"
    result = upload.upload_account_note(account_id, f"{marker}: this one should be findable.", "CSM")
    uploaded_notes.append(result["id"])

    own_results = retrieval.retrieve_unstructured(f"{marker}", account_id=account_id, top_k=5)
    assert any(marker in r["text"] for r in own_results)


def test_upload_rejects_nonexistent_account():
    with pytest.raises(upload.UploadError):
        upload.upload_account_note(999999, "content", "CSM")


def test_upload_rejects_invalid_category():
    with pytest.raises(upload.UploadError):
        upload.upload_enablement_content("title", "not_a_real_category", "content")
