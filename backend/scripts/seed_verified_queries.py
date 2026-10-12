"""
Loads verified_queries.json into the verified_queries table and embeds
each question - verified_queries.json stays the human-edited source of
truth (each entry's provenance recorded in verified_via, normally derived
from a passing eval run), this script is just what pushes it into the
place find_verified_match() actually reads from.

Truncate-and-reinsert, like seed_db.py and seed_unstructured.py - safe to
re-run any time verified_queries.json changes.

Usage: python -m scripts.seed_verified_queries   (run from backend/)
"""

import json

import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from app.database import DATABASE_URL

load_dotenv()

VERIFIED_QUERIES_PATH = "data/verified_queries.json"
MODEL_NAME = "all-MiniLM-L6-v2"


def main():
    with open(VERIFIED_QUERIES_PATH) as f:
        entries = json.load(f)

    model = SentenceTransformer(MODEL_NAME)
    embeddings = model.encode([e["question"] for e in entries], normalize_embeddings=True)

    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute("TRUNCATE verified_queries RESTART IDENTITY")
            for entry, vec in zip(entries, embeddings):
                cur.execute(
                    """INSERT INTO verified_queries (question, sql, verified_via, embedding)
                       VALUES (%s, %s, %s, %s)""",
                    (entry["question"], entry["sql"], entry["verified_via"], vec),
                )
        conn.commit()

    print(f"Loaded {len(entries)} verified queries.")


if __name__ == "__main__":
    main()
