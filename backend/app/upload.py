"""
Handles POST /upload - adds a new account note or piece of enablement
content, chunks it with the same chunker seed_unstructured.py uses, and
embeds the new chunks immediately (embedding.py) so they're retrievable
right away instead of waiting for a batch job to notice them.

account_id is the actual isolation boundary retrieval.py enforces, and
this is where that boundary either holds or doesn't at ingestion time:
an account note always carries a real account_id, resolved from a
validated foreign key (_require_account below raises if the account
doesn't exist - never silently proceeds with a bad or missing one), and
enablement content never carries an account_id at all. There's no
in-between state where a chunk could end up ambiguously scoped.
"""

from datetime import date

from app import hydra_store
from app.chunking import chunk_and_insert
from app.database import get_connection, DATABASE_URL
from app.embedding import embed_pending_chunks

import psycopg


class UploadError(Exception):
    pass


def _require_account(conn, account_id: int) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM accounts WHERE account_id = %s", (account_id,))
        if cur.fetchone() is None:
            raise UploadError(f"No account with account_id={account_id} - check the ID and try again.")


def _index_in_hydra(doc: dict):
    """
    Mirrors a just-saved document into HydraDB when it's configured.
    Postgres has already committed by the time this runs and stays the
    system of record, so a HydraDB failure here is reported back to the
    caller (hydra_indexed: False) rather than failing an upload that did
    succeed - `python -m scripts.seed_hydra` re-syncs anything missed.

    Returns None when HydraDB isn't configured, so the response can tell
    "not indexed" apart from "nowhere to index it."
    """
    if not hydra_store.is_enabled():
        return None
    try:
        hydra_store.ingest_documents([doc])
        return True
    except Exception:
        return False


def upload_account_note(account_id: int, content: str, author_role: str) -> dict:
    if not content.strip():
        raise UploadError("Note content can't be empty.")

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                _require_account(conn, account_id)
                cur.execute("SELECT name FROM accounts WHERE account_id = %s", (account_id,))
                account_name = cur.fetchone()[0]
                cur.execute(
                    """INSERT INTO account_notes (account_id, author_role, note_date, content)
                       VALUES (%s, %s, %s, %s) RETURNING note_id""",
                    (account_id, author_role, date.today(), content.strip()),
                )
                note_id = cur.fetchone()[0]
                chunks_created = chunk_and_insert(cur, "account_note", note_id, account_id, content.strip())
            conn.commit()
    except psycopg.Error as e:
        raise UploadError(str(e).strip()) from e

    chunks_embedded = embed_pending_chunks(DATABASE_URL)
    today = date.today().isoformat()
    hydra_indexed = _index_in_hydra({
        "id": hydra_store.knowledge_id("account_note", note_id),
        "title": f"Account note - {account_name} ({today}, {author_role})",
        "text": content.strip(),
        "source_type": "account_note",
        "account_id": account_id,
        "extra": {"record_id": note_id, "account_name": account_name, "author_role": author_role, "note_date": today},
    })
    return {
        "id": note_id,
        "chunks_created": chunks_created,
        "chunks_embedded": chunks_embedded,
        "hydra_indexed": hydra_indexed,
    }


def upload_enablement_content(title: str, category: str, content: str) -> dict:
    if not content.strip():
        raise UploadError("Content can't be empty.")
    if not title.strip():
        raise UploadError("Title can't be empty.")

    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO enablement_content (title, category, content)
                       VALUES (%s, %s, %s) RETURNING content_id""",
                    (title.strip(), category, content.strip()),
                )
                content_id = cur.fetchone()[0]
                chunks_created = chunk_and_insert(cur, "enablement_content", content_id, None, content.strip())
            conn.commit()
    except psycopg.Error as e:
        raise UploadError(str(e).strip()) from e

    chunks_embedded = embed_pending_chunks(DATABASE_URL)
    hydra_indexed = _index_in_hydra({
        "id": hydra_store.knowledge_id("enablement_content", content_id),
        "title": title.strip(),
        "text": f"# {title.strip()}\n\n{content.strip()}",
        "source_type": "enablement_content",
        "account_id": None,
        "extra": {"record_id": content_id, "category": category},
    })
    return {
        "id": content_id,
        "chunks_created": chunks_created,
        "chunks_embedded": chunks_embedded,
        "hydra_indexed": hydra_indexed,
    }
