"""
HydraDB - the "company brain" layer: shared knowledge, per-user memory,
and the context graph connecting them. Everything that talks to HydraDB
lives in this one module, so the rest of the app only ever sees plain
dicts and never an SDK type.

Three things live there, mapped the way HydraDB's own company-brain
pattern recommends (one database, one collection for all shared
knowledge, isolation by metadata filter - not a collection per account):

  - Knowledge: every account note and piece of enablement content, in
    KNOWLEDGE_COLLECTION. Each source carries a schema-declared `scope`
    ("global" for enablement content, "account_<id>" for a note), and
    every search filters on it server-side - the same isolation boundary
    retrieval.py enforces in its pgvector WHERE clause, moved to where
    the vectors now live.
  - Memory: what the brain has learned about one person - who they are,
    what they cover, how they like answers - in a collection of their
    own (user_<id>), so one user's memories can never meet another's in
    a search. One short fact per memory, written when a turn reveals
    one (see remember_fact for why not every turn).
  - Graph: HydraDB builds entity/relation triplets from the ingested
    text on its own; seed_hydra.py additionally declares the structured
    facts Postgres already knows (which CSM owns which account, which
    workload a contract is on) as a bring-your-own graph, so a question
    can traverse from a note to a row-level fact and back.

Opt-in by configuration: with no HYDRA_DB_API_KEY set, is_enabled() is
False and retrieval.py keeps using pgvector exactly as before - local dev,
CI and the existing test suite need no HydraDB account. With it set,
HydraDB becomes the primary path and pgvector the fallback if a call
fails, so a network blip degrades an answer's sourcing rather than
breaking the answer.
"""

import hashlib
import io
import json
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from dotenv import load_dotenv

load_dotenv()

DATABASE = os.environ.get("HYDRA_DB_DATABASE", "traceview_brain")
KNOWLEDGE_COLLECTION = os.environ.get("HYDRA_DB_COLLECTION", "gtm_knowledge")
# "fast" is one retrieval pass; "thinking" adds query expansion, reranking
# and graph traversal at the cost of latency (measured against the live
# index: ~0.3s vs ~1.2s). Two settings, because the two kinds of search
# want different things:
#   - QUERY_MODE, for company-wide playbook search: fast. The answer is
#     in the text of the playbook; there's no relationship to traverse.
#   - ACCOUNT_QUERY_MODE, for account-scoped search: thinking. This is
#     where the graph matters (who's on the account, what it's contracted
#     on, its contract status), and fast mode returns no graph paths at
#     all - confirmed live: the same query came back with 0 paths in fast
#     mode and 7 in thinking mode.
QUERY_MODE = os.environ.get("HYDRA_DB_QUERY_MODE", "fast")
ACCOUNT_QUERY_MODE = os.environ.get("HYDRA_DB_ACCOUNT_QUERY_MODE", "thinking")
GLOBAL_SCOPE = "global"

# Declared at database creation - `scope` is the hot-path isolation
# filter, so it belongs in the schema (fast, validated) rather than in
# free-form additional_metadata. The second field is `doc_kind`, not
# `source_type` as it's called everywhere else in this app: HydraDB
# reserves that name (it's a field on every chunk it returns) and rejects
# a schema declaring it with a bare 400 INVALID_INPUT.
METADATA_SCHEMA = [
    {"name": "scope", "data_type": "VARCHAR", "max_length": 64},
    {"name": "doc_kind", "data_type": "VARCHAR", "max_length": 64},
]

INGEST_BATCH_SIZE = 10
MAX_GRAPH_PATHS = 8

_client = None
_client_lock = threading.Lock()


class HydraError(Exception):
    pass


def is_enabled() -> bool:
    if os.environ.get("RETRIEVAL_BACKEND", "").lower() == "pgvector":
        return False
    return bool(os.environ.get("HYDRA_DB_API_KEY"))


def _get_client():
    # Imported lazily: the SDK is only needed once HydraDB is actually
    # configured, so a checkout without it installed still runs on pgvector.
    global _client
    with _client_lock:
        if _client is None:
            from hydra_db import HydraDB

            _client = HydraDB(token=os.environ["HYDRA_DB_API_KEY"])
        return _client


def scope_for(account_id) -> str:
    return GLOBAL_SCOPE if account_id is None else f"account_{account_id}"


def user_collection(user_id: str) -> str:
    """
    One collection per person - HydraDB's own pattern for memories, and
    the reason one user's remembered context can't surface in another's
    answer. Normalized, since a user id here is whatever the client sent.
    """
    slug = re.sub(r"[^a-z0-9]+", "_", (user_id or "").lower()).strip("_")
    return f"user_{slug or 'anonymous'}"[:64]


# ---------------------------------------------------------------------
# Database lifecycle
# ---------------------------------------------------------------------

def ensure_database(timeout: int = 300) -> None:
    """Creates DATABASE if needed and blocks until it can accept data."""
    from hydra_db.errors import ConflictError

    client = _get_client()
    try:
        client.databases.create(database=DATABASE, database_metadata_schema=METADATA_SCHEMA)
    except ConflictError:
        pass

    deadline = time.time() + timeout
    while time.time() < deadline:
        status = client.databases.status(database=DATABASE)
        if getattr(status.data.infra, "ready_for_ingestion", False):
            return
        time.sleep(3)
    raise HydraError(f"HydraDB database {DATABASE!r} was not ready within {timeout}s")


def wait_for_indexing(ids: list, collection: str = None, timeout: int = 600, graph: bool = False, on_progress=None) -> dict:
    """
    Blocks until every id is searchable ("graph_creation") - or fully
    graphed ("completed") when graph=True. Returns {id: status}.
    """
    client = _get_client()
    collection = collection or KNOWLEDGE_COLLECTION
    done = {"completed"} if graph else {"graph_creation", "completed"}
    deadline = time.time() + timeout
    states = {}

    while time.time() < deadline:
        states = {}
        for start in range(0, len(ids), 50):
            batch = ids[start:start + 50]
            status = client.context.status(database=DATABASE, collection=collection, ids=batch)
            for item in status.data.statuses or []:
                states[item.id] = item.indexing_status
                if item.indexing_status in ("errored", "failed"):
                    raise HydraError(
                        f"Indexing failed for {item.id}: {item.error_message or item.message or item.error_code}"
                    )
        if on_progress:
            on_progress(states)
        if len(states) >= len(ids) and all(s in done for s in states.values()):
            return states
        time.sleep(4)

    raise HydraError(f"Indexing did not finish within {timeout}s: {states}")


# ---------------------------------------------------------------------
# Knowledge
# ---------------------------------------------------------------------

def knowledge_id(source_type: str, source_id) -> str:
    return f"{source_type}_{source_id}"


def ingest_documents(docs: list) -> list:
    """
    docs: [{"id", "title", "text", "source_type", "account_id" (or None),
    "extra" (free-form additional_metadata), "graph" (optional
    {"entities": {...}, "relations": [...]})}, ...]

    Upserts by id, so re-running a seed or re-uploading a note replaces
    the existing source rather than duplicating it. Returns the ids
    HydraDB accepted, for wait_for_indexing().
    """
    client = _get_client()
    accepted = []

    for start in range(0, len(docs), INGEST_BATCH_SIZE):
        batch = docs[start:start + INGEST_BATCH_SIZE]
        files = []
        metadata = []
        graph_payload = {}

        for doc in batch:
            filename = re.sub(r"[^\w .()-]+", " ", doc["title"]).strip()[:120] + ".md"
            files.append((filename, io.BytesIO(doc["text"].encode("utf-8")), "text/markdown"))
            additional = {"doc_title": doc["title"][:200], **(doc.get("extra") or {})}
            if doc.get("account_id") is not None:
                additional["account_id"] = doc["account_id"]
            metadata.append({
                "id": doc["id"],
                "metadata": {"scope": scope_for(doc.get("account_id")), "doc_kind": doc["source_type"]},
                "additional_metadata": additional,
            })
            if doc.get("graph"):
                graph_payload[doc["id"]] = doc["graph"]

        kwargs = {
            "database": DATABASE,
            "collection": KNOWLEDGE_COLLECTION,
            "type": "knowledge",
            "documents": files,
            "document_metadata": json.dumps(metadata),
            "upsert": "true",
        }
        if graph_payload:
            kwargs["graph_payload"] = json.dumps(graph_payload)

        result = client.context.ingest(**kwargs)
        for item in result.data.results or []:
            if item.error:
                raise HydraError(f"HydraDB rejected {item.id or item.filename}: {item.error}")
            accepted.append(item.id)

    return accepted


def list_knowledge_ids() -> list:
    client = _get_client()
    ids = []
    page = 1
    while True:
        result = client.context.list(
            database=DATABASE, collection=KNOWLEDGE_COLLECTION, type="knowledge", page=page, page_size=100
        )
        batch = result.data.sources or []
        ids.extend(item.id for item in batch)
        if len(batch) < 100:
            return ids
        page += 1


def delete_knowledge(ids: list) -> dict:
    """
    Returns {id: None if deleted, else the reason it wasn't}. HydraDB
    refuses to delete a source whose graph is still being built ("Source
    is still processing; retry deletion after ingestion completes") - a
    per-item refusal inside a 200 response, not an exception, so the
    caller has to read the result rather than assume the delete happened.
    """
    if not ids:
        return {}
    result = _get_client().context.delete(
        database=DATABASE, collection=KNOWLEDGE_COLLECTION, type="knowledge", ids=ids
    )
    return {item.id: (None if item.deleted else item.error or "not deleted") for item in result.data.results or []}


def _triplet_text(triplet) -> str:
    def name(node):
        if isinstance(node, dict):
            return node.get("name") or ""
        return getattr(node, "name", "") or ""

    relation = triplet.relation or {}
    predicate = relation.get("canonical_predicate") or relation.get("predicate") or "RELATED_TO"
    return f"{name(triplet.source)} --[{predicate}]--> {name(triplet.target)}"


def _graph_paths(graph_context) -> list:
    """
    The relation paths HydraDB's graph found relevant to the query,
    flattened to one readable line per path ("A --[MANAGED_BY]--> B  ·
    B --[...]--> C") - what the synthesis prompt and the reasoning trace
    both show, instead of the raw triplet objects.
    """
    if graph_context is None:
        return []

    paths = []
    seen = set()
    for path in (graph_context.query_paths or []) + (graph_context.chunk_relations or []):
        triplets = path.triplets or []
        text = "  ·  ".join(_triplet_text(t) for t in triplets)
        if not text or text in seen:
            continue
        seen.add(text)
        # The sentence each relation was declared with or extracted from -
        # what lets the answer cite the relationship, not just assert it.
        evidence = " ".join(
            str((t.relation or {}).get("context")).strip() for t in triplets if (t.relation or {}).get("context")
        )
        paths.append({"path": text, "score": path.relevancy_score, "context": evidence or None})
        if len(paths) >= MAX_GRAPH_PATHS:
            break
    return paths


GLOBAL_RESULTS_WHEN_SCOPED = 2


def _query_knowledge(question: str, scopes: list, top_k: int, mode: str):
    return _get_client().query(
        database=DATABASE,
        collection=KNOWLEDGE_COLLECTION,
        query=question,
        type="knowledge",
        query_by="hybrid",
        mode=mode,
        max_results=top_k,
        graph_context=True,
        query_apps=False,
        metadata_filters={"scope": {"contains_any": scopes}},
    )


def _sources_from(result, scopes: list) -> list:
    sources = []
    for chunk in result.data.chunks or []:
        metadata = chunk.metadata or {}
        additional = chunk.additional_metadata or {}
        if metadata.get("scope") not in scopes:
            continue
        account_id = additional.get("account_id")
        sources.append({
            "chunk_id": chunk.chunk_uuid,
            "source_type": metadata.get("doc_kind") or "knowledge",
            "source_id": additional.get("record_id"),
            "account_id": int(account_id) if account_id is not None else None,
            "title": additional.get("doc_title") or chunk.source_title,
            "text": (chunk.chunk_content or "").strip(),
            "score": float(chunk.relevancy_score or 0.0),
        })
    return sources


def search_knowledge(question: str, account_ids=None, top_k: int = 4) -> dict:
    """
    Scoped the same way retrieval.retrieve_unstructured always has been:
    an account-scoped search sees those accounts' notes plus global
    enablement content; an unscoped one sees enablement content only.
    The filter is applied by HydraDB before ranking - and re-checked on
    the way out (_sources_from), so a leak would need both the
    server-side filter and this app's own check to fail at once.

    An account-scoped search is two queries, not one filtered to both
    scopes. Confirmed against the live index: asked why one account's
    consumption was declining, a single combined query returned four
    generic playbooks about consumption decline and ranked that
    account's own note sixth - the general material is simply a closer
    textual match to a general-sounding question than one specific QBR
    note is. So the account's notes are fetched on their own (top_k of
    them, never crowded out) and a small fixed number of playbooks is
    added alongside.

    Returns {"sources": [...], "graph_paths": [...], "request_id": ...}
    with sources in retrieval.py's existing shape. request_id is the
    query feedback gets attached to - the account query when there is one.
    """
    account_scopes = [scope_for(a) for a in (account_ids or [])]

    if not account_scopes:
        result = _query_knowledge(question, [GLOBAL_SCOPE], top_k, QUERY_MODE)
        return {
            "sources": _sources_from(result, [GLOBAL_SCOPE]),
            "graph_paths": _graph_paths(result.data.graph_context),
            "request_id": getattr(result.meta, "request_id", None),
        }

    with ThreadPoolExecutor(max_workers=2) as pool:
        account_future = pool.submit(_query_knowledge, question, account_scopes, top_k, ACCOUNT_QUERY_MODE)
        global_future = pool.submit(_query_knowledge, question, [GLOBAL_SCOPE], GLOBAL_RESULTS_WHEN_SCOPED, QUERY_MODE)
        account_result = account_future.result()
        global_result = global_future.result()

    paths = _graph_paths(account_result.data.graph_context)
    seen = {p["path"] for p in paths}
    for p in _graph_paths(global_result.data.graph_context):
        if p["path"] not in seen and len(paths) < MAX_GRAPH_PATHS:
            paths.append(p)

    return {
        "sources": _sources_from(account_result, account_scopes) + _sources_from(global_result, [GLOBAL_SCOPE]),
        "graph_paths": paths,
        "request_id": getattr(account_result.meta, "request_id", None),
    }


# ---------------------------------------------------------------------
# Memory
# ---------------------------------------------------------------------

def recall_memories(user_id: str, question: str, top_k: int = 4) -> list:
    """
    What the brain remembers about this user that's relevant to this
    question. An empty list for a user it has never met - their
    collection doesn't exist until the first memory is written, which is
    a normal first-conversation state, not an error.
    """
    from hydra_db.core.api_error import ApiError

    try:
        result = _get_client().query(
            database=DATABASE,
            collection=user_collection(user_id),
            query=question,
            type="memory",
            query_by="hybrid",
            mode="fast",
            max_results=top_k,
            graph_context=False,
        )
    except ApiError as e:
        if e.status_code in (400, 404):
            return []
        raise

    return [
        {"id": chunk.id, "text": (chunk.chunk_content or "").strip(), "score": float(chunk.relevancy_score or 0.0)}
        for chunk in (result.data.chunks or [])
        if (chunk.chunk_content or "").strip()
    ]


def remember_fact(user_id: str, text: str, origin: str = "explicit") -> str:
    """
    Stores one fact about a user, verbatim (infer=False).

    Deliberately not "hand HydraDB every exchange with infer=True and let
    it work out what matters" - that was the first version, and against
    the live service it kept the raw question/answer pair of every turn as
    a memory, inferred fact or not. Recall for "which of my accounts are
    at risk" then returned old Q&A about business data alongside (and
    sometimes instead of) "the user is a CSM", which is the one thing
    that actually needed remembering. So what's worth keeping is decided
    upstream - agent.py's routing call already reads the message and
    restates any durable fact in it - and only that sentence lands here.

    The id is derived from the text, so being told the same thing twice
    upserts one memory instead of accumulating duplicates. The text also
    goes in as the title and into additional_metadata: HydraDB's list
    endpoint returns a memory's title and metadata but not its body, and
    the Memory page needs something to show.
    """
    text = text.strip()
    memory_id = hashlib.sha1(re.sub(r"\s+", " ", text.lower()).encode("utf-8")).hexdigest()[:32]
    result = _get_client().context.ingest(
        database=DATABASE,
        collection=user_collection(user_id),
        type="memory",
        memories=json.dumps([{
            "id": memory_id,
            "title": text[:120],
            "text": text,
            "infer": False,
            "user_name": user_id,
            "additional_metadata": {"origin": origin, "fact": text[:600]},
        }]),
        upsert="true",
    )
    results = result.data.results or []
    return (results[0].id if results else None) or memory_id


def remember_fact_async(user_id: str, text: str, origin: str = "conversation") -> None:
    """
    Fire-and-forget: writing a memory must never delay or fail the answer
    it was learned from. A lost memory write costs a little recall later;
    a blocked response costs the user right now.
    """
    def run():
        try:
            remember_fact(user_id, text, origin)
        except Exception:
            pass

    threading.Thread(target=run, daemon=True).start()


def list_memories(user_id: str, limit: int = 50) -> list:
    from hydra_db.core.api_error import ApiError

    try:
        result = _get_client().context.list(
            database=DATABASE,
            collection=user_collection(user_id),
            type="memory",
            page=1,
            page_size=limit,
        )
    except ApiError as e:
        if e.status_code in (400, 404):
            return []
        raise

    memories = []
    for item in result.data.user_memories or []:
        additional = item.additional_metadata or {}
        memories.append({
            "id": item.memory_id,
            "text": (additional.get("fact") or item.title or "").strip(),
            "timestamp": item.timestamp,
            "origin": additional.get("origin"),
        })
    return memories


def forget_memory(user_id: str, memory_id: str) -> None:
    _get_client().context.delete(
        database=DATABASE,
        collection=user_collection(user_id),
        type="memory",
        ids=[memory_id],
    )


# ---------------------------------------------------------------------
# Feedback + status
# ---------------------------------------------------------------------

def submit_feedback(request_id: str, rating: str, comment: str = None) -> None:
    """
    Closes the loop on one specific HydraDB query (request_id is the one
    its response carried) - a thumbs-up/down on an answer becomes signal
    about the retrieval that produced it.
    """
    kwargs = {"request_id": request_id, "rating": rating, "source": "user", "database": DATABASE}
    kwargs["feedback"] = comment or f"User rated this answer {rating}."
    _get_client().feedback.submit(**kwargs)


def status() -> dict:
    """What the UI's brain badge shows - never raises, a status check failing isn't an outage."""
    if not is_enabled():
        return {"enabled": False, "backend": "pgvector"}

    info = {
        "enabled": True,
        "backend": "hydradb",
        "database": DATABASE,
        "collection": KNOWLEDGE_COLLECTION,
        "query_mode": QUERY_MODE,
        "account_query_mode": ACCOUNT_QUERY_MODE,
    }
    try:
        stats = _get_client().databases.stats(database=DATABASE).data
        for key in ("knowledge_collection", "memory_collection"):
            value = getattr(stats, key, None)
            if value is not None:
                info[key] = value.dict() if hasattr(value, "dict") else value
    except Exception as e:
        info["error"] = type(e).__name__
    return info
