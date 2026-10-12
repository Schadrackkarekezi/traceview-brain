"""
Shared word-based chunker - used both by seed_unstructured.py (bulk
authoring) and the live /upload endpoint (one document at a time), so
there's exactly one chunking implementation to keep consistent rather
than two copies that could quietly drift apart.
"""

CHUNK_SIZE = 120  # words
CHUNK_OVERLAP = 20


def chunk_text(text: str, chunk_size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list:
    words = text.split()
    if len(words) <= chunk_size:
        return [text]
    chunks = []
    start = 0
    while start < len(words):
        end = start + chunk_size
        chunks.append(" ".join(words[start:end]))
        if end >= len(words):
            break
        start = end - overlap
    return chunks


def chunk_and_insert(cur, source_type: str, source_id: int, account_id, text: str) -> int:
    """
    Chunks text and inserts every chunk into document_chunks in one
    executemany - the same two steps both seed_unstructured.py (bulk
    authoring) and app/upload.py (one document at a time via POST /upload)
    need, so this is the one place that pairing lives.
    """
    chunks = chunk_text(text)
    rows = [(source_type, source_id, account_id, i, chunk) for i, chunk in enumerate(chunks)]
    cur.executemany(
        """INSERT INTO document_chunks (source_type, source_id, account_id, chunk_index, chunk_text)
           VALUES (%s, %s, %s, %s, %s)""",
        rows,
    )
    return len(chunks)
