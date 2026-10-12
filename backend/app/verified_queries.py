"""
Verified query repository - a small set of question -> SQL pairs that have
already passed both the deterministic checks and the LLM judge in the eval
suite (see verified_queries.json's "verified_via" field, and eval_set.json).
"Verified" means "passed the eval suite," not "someone eyeballed it once."

On /ask, if the incoming question closely matches one of these, the agent
skips LLM generation entirely and runs the pre-verified SQL directly -
still through the exact same query_engine.run_select() safety guard as
every other query, just skipping the (cost- and latency-incurring)
generation step. This is a real optimization, not just a trust badge: a
verified match costs $0 and runs in milliseconds instead of a few cents
and a couple of seconds.

Backed by pgvector (see the verified_queries table and
seed_verified_queries.py), not an in-memory FAISS index rebuilt from JSON
on every process start - the embeddings live precomputed in Postgres,
alongside the structured data, matched here the same way retrieval.py
matches document_chunks.

Threshold calibration matters more here than in document retrieval,
because a false-positive match means silently serving the WRONG cached
answer instead of just missing a retrieval. Empirically (see git history
for the calibration script): true paraphrases of the same question score
0.92-1.0 cosine similarity; a *related but meaningfully different*
question - "what is our win rate" vs "what is our win rate BY
DEPARTMENT" - still scores 0.80, since embedding models cluster on
topic, not exact scope. MATCH_THRESHOLD is set well above that gap.
"""

import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector

from app.database import READONLY_DATABASE_URL
from app.embedding import _get_model

load_dotenv()

MATCH_THRESHOLD = 0.92


def find_verified_match(question: str):
    query_vec = _get_model().encode(question, normalize_embeddings=True)

    with psycopg.connect(READONLY_DATABASE_URL) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT question, sql, 1 - (embedding <=> %s) AS score
                FROM verified_queries
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s
                LIMIT 1
                """,
                (query_vec, query_vec),
            )
            row = cur.fetchone()

    if row is None or row[2] < MATCH_THRESHOLD:
        return None

    matched_question, sql, score = row
    return {"question": matched_question, "sql": sql, "score": float(score)}
