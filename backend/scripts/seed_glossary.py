"""
Loads glossary.json into the glossary_terms table and embeds each term -
same pattern as seed_verified_queries.py. glossary.json stays the human-
edited source; this is what agent.py's retrieval actually reads from.

Usage: python -m scripts.seed_glossary   (run from backend/)
"""

import json

import psycopg
from dotenv import load_dotenv
from pgvector.psycopg import register_vector
from sentence_transformers import SentenceTransformer

from app.database import DATABASE_URL

load_dotenv()

GLOSSARY_PATH = "data/glossary.json"
MODEL_NAME = "all-MiniLM-L6-v2"


def main():
    with open(GLOSSARY_PATH) as f:
        entries = json.load(f)

    model = SentenceTransformer(MODEL_NAME)
    texts = [f"{e['term']}: {e['definition']}" for e in entries]
    embeddings = model.encode(texts, normalize_embeddings=True)

    with psycopg.connect(DATABASE_URL) as conn:
        register_vector(conn)
        with conn.cursor() as cur:
            cur.execute("TRUNCATE glossary_terms RESTART IDENTITY")
            for entry, vec in zip(entries, embeddings):
                cur.execute(
                    "INSERT INTO glossary_terms (term, definition, embedding) VALUES (%s, %s, %s)",
                    (entry["term"], entry["definition"], vec),
                )
        conn.commit()

    print(f"Loaded {len(entries)} glossary terms.")


if __name__ == "__main__":
    main()
