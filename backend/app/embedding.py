"""
Embeds every document_chunks row still missing an embedding - shared by
embed_content.py (the bulk backfill script) and the live /upload endpoint,
which calls this right after inserting new chunks so they're retrievable
immediately instead of waiting for a separate batch job to notice them.

_get_model() is also the one place the SentenceTransformer singleton is
loaded - retrieval.py and verified_queries.py both import it from here
rather than each keeping their own copy of the same lazy-load, so the
model is loaded once per process no matter how many of the three modules
end up used.
"""

import psycopg
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

MODEL_NAME = "all-MiniLM-L6-v2"

_model = None


def _get_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model


def embed_pending_chunks(database_url: str) -> int:
    model = _get_model()

    with psycopg.connect(database_url) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute("SELECT chunk_id, chunk_text FROM document_chunks WHERE embedding IS NULL")
            rows = cur.fetchall()

            if not rows:
                return 0

            texts = [r[1] for r in rows]
            embeddings = model.encode(texts, normalize_embeddings=True)

            for (chunk_id, _), vec in zip(rows, embeddings):
                cur.execute("UPDATE document_chunks SET embedding = %s WHERE chunk_id = %s", (vec, chunk_id))

        conn.commit()

    return len(rows)
