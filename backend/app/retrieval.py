"""
Unstructured retrieval over document_chunks, backed by pgvector - the
production-grade replacement for the old in-memory FAISS index (rag.py,
deleted in the Postgres rewrite). Same embedding model, same normalize +
cosine-similarity approach; the real difference is where the vectors
live: inside Postgres, alongside the structured data they get combined
with for hybrid answers, instead of a separate in-memory index rebuilt
from scratch on every process start.

Connects through the same traceview_readonly role query_engine.py uses -
this is a SELECT-only workload, so it gets the same least-privilege
connection as everything else that touches the database on the agent's
behalf, not a separate admin connection just because it's a different
kind of query.

account_id filtering happens in the SQL WHERE clause, not by filtering
results after the fact - an account-scoped call only ever sees that
account's notes plus global enablement content, never another account's
notes. This is the actual isolation boundary Phase 11's upload feature
depends on; filtering after retrieval would only be a UI-layer illusion
of isolation, not a real one.
"""

import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector

from app import hydra_store
from app.database import READONLY_DATABASE_URL
from app.embedding import _get_model

load_dotenv()


def retrieve_context(question: str, account_id=None, top_k: int = 3) -> dict:
    """
    What agent.py actually calls for an "unstructured"/"hybrid" turn -
    HydraDB when it's configured, pgvector otherwise, and pgvector as the
    fallback if the HydraDB call fails, so losing the network degrades
    where an answer's context came from rather than losing the answer.

    Same scoping contract either way (see retrieve_unstructured below);
    the difference is what comes back alongside the chunks: HydraDB also
    returns graph_paths, the entity relationships its graph found
    relevant to the question, which pgvector has no equivalent of.

    Returns {"sources", "graph_paths", "backend", "hydra_request_id",
    "fallback_reason"} - backend is which store really answered, not
    which one was configured.
    """
    if isinstance(account_id, (list, tuple, set)):
        account_ids = list(account_id)
    else:
        account_ids = [account_id] if account_id is not None else []

    fallback_reason = None
    if hydra_store.is_enabled():
        try:
            result = hydra_store.search_knowledge(question, account_ids=account_ids, top_k=top_k)
            return {
                "sources": result["sources"],
                "graph_paths": result["graph_paths"],
                "backend": "hydradb",
                "hydra_request_id": result["request_id"],
                "fallback_reason": None,
            }
        except Exception as e:
            fallback_reason = type(e).__name__

    return {
        "sources": retrieve_unstructured(question, account_id=account_ids or None, top_k=top_k),
        "graph_paths": [],
        "backend": "pgvector",
        "hydra_request_id": None,
        "fallback_reason": fallback_reason,
    }


def retrieve_unstructured(question: str, account_id=None, top_k: int = 3) -> list:
    """
    account_id: None (global enablement content only), a single account
    id, or a list of account ids (a genuine multi-account hybrid
    question, e.g. "compare accounts X and Y") - normalized to a list
    either way, so the SQL is always one query with `= ANY(...)`, not N
    separate ones. Never all accounts' notes indiscriminately regardless
    of which form this takes - that would be an isolation violation
    waiting to happen, just with no account_id(s) in hand yet to violate
    it against.
    """
    if isinstance(account_id, (list, tuple, set)):
        account_ids = list(account_id) or None
    elif account_id is not None:
        account_ids = [account_id]
    else:
        account_ids = None

    query_vec = _get_model().encode(question, normalize_embeddings=True)

    if account_ids is not None:
        scope_clause = "(account_id = ANY(%s) OR account_id IS NULL)"
        scope_params = (account_ids,)
    else:
        scope_clause = "account_id IS NULL"
        scope_params = ()

    with psycopg.connect(READONLY_DATABASE_URL) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT chunk_id, source_type, source_id, account_id, chunk_text,
                       1 - (embedding <=> %s) AS score
                FROM document_chunks
                WHERE embedding IS NOT NULL AND {scope_clause}
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (query_vec, *scope_params, query_vec, top_k),
            )
            rows = cur.fetchall()

    return [
        {
            "chunk_id": r[0],
            "source_type": r[1],
            "source_id": r[2],
            "account_id": r[3],
            "text": r[4],
            "score": float(r[5]),
        }
        for r in rows
    ]


GLOSSARY_MATCH_THRESHOLD = 0.35


def retrieve_glossary(question: str, top_k: int = 3) -> list:
    """
    Grounds SQL generation in this project's own precise term definitions
    ("active deal", "win rate") instead of the model inferring a generic
    one - this is what agent.py's system prompt calls "the exact glossary
    definition below." Replaces rag.py's old in-memory FAISS index with
    the same pgvector pattern as everything else in this file.

    GLOSSARY_MATCH_THRESHOLD exists because top_k alone isn't enough -
    confirmed in eval: a follow-up asking "how many of those are at_risk"
    pulled in "under-consumption" (score 0.291, real noise - measured
    against real matches landing at 0.38-0.65) just to fill 3 slots, and
    the judge then penalized the agent for not using that unrelated
    definition. Better to return fewer terms than pad with noise.
    """
    query_vec = _get_model().encode(question, normalize_embeddings=True)

    with psycopg.connect(READONLY_DATABASE_URL) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT term, definition, 1 - (embedding <=> %s) AS score
                FROM glossary_terms
                WHERE embedding IS NOT NULL
                ORDER BY embedding <=> %s
                LIMIT %s
                """,
                (query_vec, query_vec, top_k),
            )
            rows = cur.fetchall()

    return [
        {"term": r[0], "definition": r[1], "score": float(r[2])}
        for r in rows
        if r[2] >= GLOSSARY_MATCH_THRESHOLD
    ]
