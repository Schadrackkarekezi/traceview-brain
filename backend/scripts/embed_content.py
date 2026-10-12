"""
CLI wrapper for the bulk backfill case - the actual logic lives in
embedding.py so the live /upload endpoint can call the same function
instead of duplicating it.

Usage: python -m scripts.embed_content   (run from backend/)
"""

from app.database import DATABASE_URL
from app.embedding import embed_pending_chunks


def main():
    count = embed_pending_chunks(DATABASE_URL)
    print(f"Embedded {count} chunks." if count else "Nothing to embed.")


if __name__ == "__main__":
    main()
