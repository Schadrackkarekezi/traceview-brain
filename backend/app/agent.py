"""
The agent behind POST /ask.

This is a multi-step pipeline, not a single LLM call:

  verified-match check -> retrieve (RAG) -> graph lookup -> respond, where
  "respond" is one of four modes the model itself chooses per turn:

    - "sql":          the question asks for numbers this schema can answer
                       directly. Generate a query, execute it, and on
                       failure retry with the error fed back to the model
                       (up to MAX_ATTEMPTS). A generated (not verified-
                       match) "sql" answer also gets a short, separate
                       plain-language note attached - see
                       _explain_sql_stream.
    - "unstructured":  the question asks for something only account notes
                       or enablement content would have - a reason, a
                       play, an objection script - not a number. No SQL;
                       instead retrieve relevant chunks (account-scoped if
                       the question named one account, global otherwise)
                       and synthesize a grounded reply from them.
    - "hybrid":        the question needs both - most often "why is X
                       declining/at risk" - a number AND the narrative
                       behind it. Generate the SQL for the number, retrieve
                       the relevant notes for the "why," and synthesize
                       one answer from both, explicitly flagging it if they
                       disagree rather than silently picking one.
    - "chat":          anything else - a greeting, a meta-question about a
                       previous answer, a clarifying question the model
                       needs to ask back, or an honest decline when
                       nothing in the schema or the notes can answer it.

Mixing these into one Pydantic response type (rather than always forcing
SQL) is what makes "can't answer this" a normal conversational turn
instead of an error: previously every non-SQL response was an exception
that surfaced in the UI as a red failure banner, which was wrong - the
agent hadn't failed, it had correctly declined to invent a query.
AgentError is now reserved for genuine failures: SQL that keeps erroring
out after every retry.

Before any of that, run_ask() checks verified_queries.py for a close
semantic match to a pre-vetted question - if found, it skips the
classification + SQL-generation call entirely and executes the verified
SQL directly, at $0 cost and millisecond latency for that part. It still
generates a short explanation of the result afterward (see
_explain_sql_stream), same as any other "sql" turn - that one part isn't
free, since the explanation has to be grounded in the question actually
asked, not the one the cached SQL was originally verified against.

When HydraDB is configured (see hydra_store.py) three more things happen
around that pipeline, none of them required for an answer: what's
remembered about the person asking is recalled in parallel with the
glossary and graph lookups and given to the routing call, so "my
accounts" resolves to theirs; unstructured/hybrid retrieval goes to
HydraDB first (pgvector if that fails) and brings back the entity
relationships its graph found relevant; and any durable fact the user
states about themselves is restated by the routing call (user_fact) and
handed back for main.py to save.

Each step is timed and recorded in `trace`, so the same data that answers
the question also documents exactly what the agent did to get there -
this trace is what the observability layer logs and displays.
"""

import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, Optional

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

from app import hydra_store
from app.costs import estimate_cost_usd
from app.database import get_connection, fetch_schema, fetch_check_constraint_values
from app.knowledge_graph import find_relevant_entities, find_join_paths, tables_by_kind
from app.query_engine import run_select, QueryError
from app.retrieval import retrieve_glossary, retrieve_context
from app.verified_queries import find_verified_match

load_dotenv()

MODEL = os.environ.get("OPENAI_MODEL", "gpt-4o")
MAX_ATTEMPTS = 3

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


class AgentResponse(BaseModel):
    response_type: Literal["sql", "chat", "unstructured", "hybrid"]
    message: Optional[str] = None  # chat mode: the reply text
    sql: Optional[str] = None  # sql / hybrid mode: the query
    account_hints: list[str] = []  # unstructured / hybrid: every account name mentioned, if any
    user_fact: Optional[str] = None  # any mode: a durable fact the user just stated about themselves


class AgentError(Exception):
    def __init__(self, message, prompt_tokens=0, completion_tokens=0):
        super().__init__(message)
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens


# A question phrased in the first person ("my accounts", "what should I
# focus on") depends on who's asking - exactly what a verified query, canned
# SQL written for nobody in particular, can't know. "our"/"we" are left out
# on purpose: "what's our win rate" is a company-wide question.
_FIRST_PERSON = re.compile(r"\b(my|mine|me|i|i'm|i've|i'd|myself)\b", re.IGNORECASE)

_memory_pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="memory-recall")


def _memory_enabled(user_id) -> bool:
    return bool(user_id) and hydra_store.is_enabled()


def _recall_memories(user_id: str, question: str):
    """
    Long-term memory lookup for one question - runs on _memory_pool,
    alongside glossary retrieval and the graph lookup rather than in
    front of them, so remembering who's asking doesn't add its full
    latency to every turn.

    Returns (memories, trace_step). A failed recall is an empty list plus
    an "error" on the step, never an exception: the question is still
    answerable without knowing who asked it, just less personally.
    """
    t0 = time.perf_counter()
    step = {"step": "memory_recall", "backend": "hydradb", "user": user_id}
    try:
        memories = hydra_store.recall_memories(user_id, question)
    except Exception as e:
        memories = []
        step["error"] = type(e).__name__
    step["latency_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    step["memories"] = [{"text": m["text"], "score": round(m["score"], 4)} for m in memories]
    return memories, step


def _format_memories(memories) -> str:
    if not memories:
        return ""
    return "\n".join(f"- {m['text']}" for m in memories)


def _format_schema(tables, check_values=None) -> str:
    """
    Grouped into dimensions (reference data) vs facts (transactional
    events) instead of one flat list - the same structural cue a
    well-designed semantic layer gives an analyst: which tables to expect
    grouping/aggregation against (facts) vs which are just lookups
    (dimensions).

    Columns constrained to a fixed set of values (activity_type, stage,
    status, etc.) get those values listed inline. Without this, the model
    can only guess a plausible-sounding literal - confirmed in testing: it
    wrote activity_type = 'POC', which isn't a real value (the actual one
    is 'poc_kickoff') and silently matched zero rows instead of erroring,
    which is a wrong answer that looks like a valid empty one.
    """
    check_values = check_values or {}
    by_name = {t["name"]: t for t in tables}
    kinds = tables_by_kind()

    lines = []
    for label, kind in [("Dimensions (reference data)", "dimension"), ("Facts (transactional events)", "fact")]:
        names = kinds.get(kind, [])
        if not names:
            continue
        lines.append(f"{label}:")
        for name in names:
            t = by_name.get(name)
            if not t:
                continue
            col_strs = []
            for c in t["columns"]:
                values = check_values.get(f"{name}.{c['name']}")
                if values:
                    col_strs.append(f"{c['name']} ({c['type']}: one of {'|'.join(values)})")
                else:
                    col_strs.append(f"{c['name']} ({c['type']})")
            lines.append(f"- {name}: {', '.join(col_strs)}")
    return "\n".join(lines)


def _most_recent_data_date(conn):
    """
    The DO rule below tells the model to ground relative time phrases in
    the data's own most recent date, not today's real date - but that
    instruction is unfollowable without actually telling it what that
    date is. Confirmed in testing: without this, "this year" resolved to
    2023 (the seed data's earliest year) instead of anything grounded.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT MAX(d) FROM (
                SELECT close_date AS d FROM deals WHERE close_date IS NOT NULL
                UNION ALL SELECT usage_month FROM consumption_usage
                UNION ALL SELECT activity_date FROM activities
                UNION ALL SELECT touch_date FROM marketing_touches
            ) all_dates
            """
        )
        row = cur.fetchone()
    return row[0] if row else None


def _resolve_account_ids(conn, account_hints: list[str]):
    """
    The model can only echo whatever name-like phrases appeared in the
    question - it has the schema, not the actual row data - so this does
    the real lookup against accounts.name, one ILIKE match per hint (not
    exact: "Ashford" should resolve to "Ashford Capital Partners"). A
    hint that matches nothing is silently skipped rather than guessed at
    - global-only retrieval for that one name is the safe direction to
    fail in, same as before this supported more than one hint.

    Returns (ids, names): ids is every hint that resolved, in order;
    names is {account_id: real account name}, needed so a multi-account
    hybrid/unstructured answer can label which account each retrieved
    note actually belongs to instead of blending them into one
    undifferentiated pile of text.
    """
    ids = []
    names = {}
    with conn.cursor() as cur:
        for hint in account_hints:
            if not hint:
                continue
            cur.execute("SELECT account_id, name FROM accounts WHERE name ILIKE %s LIMIT 1", (f"%{hint}%",))
            row = cur.fetchone()
            if row:
                ids.append(row[0])
                names[row[0]] = row[1]
    return ids, names


MAX_ROW_DERIVED_HINTS = 5


def _account_hints_from_rows(columns: list, rows: list) -> list:
    """
    A hybrid question often names no account at all - "why are my at-risk
    accounts declining" - yet its SQL result is exactly the list of
    accounts the "why" is about. Without this, retrieval for that turn
    is global-only (the model had no name to put in account_hints) and
    the answer explains the decline from generic enablement content
    while the accounts' own notes sit unread.

    Only name-like columns are considered, and _resolve_account_ids still
    has to match each value to a real account - so a workload or partner
    name that happens to be in the result is dropped there, not guessed at.
    Capped, since a 40-row result isn't a question about 40 accounts.
    """
    name_columns = [i for i, c in enumerate(columns) if "name" in c.lower() or "account" in c.lower()]
    hints = []
    for row in rows:
        for i in name_columns:
            value = row[i]
            if isinstance(value, str) and value not in hints:
                hints.append(value)
        if len(hints) >= MAX_ROW_DERIVED_HINTS:
            break
    return hints[:MAX_ROW_DERIVED_HINTS]


def _retrieve_account_context(question: str, account_hints: list[str], attempt: int, trace: list):
    """
    Shared by the "unstructured" and "hybrid" branches of run_ask_stream -
    both need the exact same steps (resolve hints to real account ids,
    retrieve context scoped to them, record the same trace step), so this
    is the one place that logic lives instead of two copies drifting apart.

    top_k scales with how many accounts are actually in play - 4 was
    tuned for one account and stays 4 for a single-account question, but a
    genuine multi-account comparison ("why are X and Y both declining")
    needs enough headroom that account Y's note doesn't get crowded out of
    a fixed top-4 by account X's chunks alone.

    Returns (retrieved, account_names) - retrieved is retrieve_context()'s
    dict (sources, plus graph_paths and which backend answered);
    account_names ({id: name}) is what _synthesize_stream uses to label
    which account each source belongs to.
    """
    with get_connection() as conn:
        account_ids, account_names = _resolve_account_ids(conn, account_hints)
    retrieve_top_k = 4 * max(1, len(account_ids))

    t_retrieve = time.perf_counter()
    retrieved = retrieve_context(question, account_id=account_ids or None, top_k=retrieve_top_k)
    sources = retrieved["sources"]
    step = {
        "step": "unstructured_retrieve",
        "attempt": attempt,
        "latency_ms": round((time.perf_counter() - t_retrieve) * 1000, 2),
        "backend": retrieved["backend"],
        "account_hints": account_hints,
        "resolved_account_ids": account_ids,
        "sources": [{"source_type": s["source_type"], "score": round(s["score"], 4)} for s in sources],
        "graph_paths": [p["path"] for p in retrieved["graph_paths"]],
    }
    if retrieved["fallback_reason"]:
        step["fallback_reason"] = retrieved["fallback_reason"]
    trace.append(step)
    return retrieved, account_names


def _synthesize_stream(
    question: str,
    sql_result: dict,
    sources: list,
    history: list,
    account_names: dict = None,
    graph_paths: list = None,
    memories: list = None,
):
    """
    The second LLM call for "unstructured"/"hybrid" turns - the first call
    only decided the route (and wrote SQL, for hybrid); this one actually
    writes the answer, grounded in whatever came back from execution and
    retrieval. Plain text, not structured output - there's nothing to
    parse out of prose, which is exactly what makes it streamable: unlike
    the routing call (strict JSON, unreadable until fully parsed), this
    one can be shown to the user token by token as it's written.

    account_names ({account_id: name}) labels each source by which
    account it's actually about, not just its source_type - with one
    account in play the distinction is redundant (everything's about the
    same account), but a genuine multi-account question ("why are X and Y
    both declining") needs it, or the model sees an undifferentiated pile
    of notes with no way to tell whose is whose.

    graph_paths are the entity relationships HydraDB's graph found
    relevant (empty on the pgvector path, which has no graph) - evidence
    of how things connect, given to the model as its own labelled block
    rather than mixed into the note text. memories are what's remembered
    about the person asking: context for how to frame the answer, never
    evidence about an account.

    Yields ("delta", text) for each chunk as it arrives, then a final
    ("usage", {...}) once the stream ends.
    """
    parts = [f"Question: {question}"]

    if sql_result:
        preview = sql_result["rows"][:10]
        parts.append(
            f"SQL query result - columns: {sql_result['columns']}\nrows (first 10): {preview}"
        )

    if sources:
        def _label(s):
            account_name = (account_names or {}).get(s.get("account_id"))
            source = f"{s['source_type']} - {account_name}" if account_name else s["source_type"]
            return f"[{source}, similarity {s['score']:.2f}]"

        source_text = "\n\n".join(f"{_label(s)} {s['text']}" for s in sources)
        parts.append(f"Retrieved context:\n{source_text}")
    else:
        parts.append("Retrieved context: (nothing matched closely enough to be useful)")

    if graph_paths:
        parts.append(
            "Knowledge-graph relationships relevant to the question (how the entities above "
            "connect - who is on which account, what it is contracted on):\n"
            + "\n".join(f"- {p['path']}" + (f" ({p['context']})" if p.get("context") else "") for p in graph_paths)
        )

    if memories:
        parts.append(
            "What you remember about the person asking, from earlier conversations - use it to "
            "decide emphasis and format, not as evidence about any account:\n"
            + _format_memories(memories)
        )

    history_text = _format_history(history)
    if history_text:
        parts.append(history_text)

    multi_account_note = (
        "\n\nThe retrieved context spans more than one account (see the account name in each "
        "source's label) - compare and contrast them explicitly, don't blend them into one "
        "undifferentiated narrative. If one account's cause isn't covered by its own retrieved "
        "notes, say so for that account specifically rather than borrowing another account's "
        "reason for it."
        if account_names and len(account_names) > 1
        else ""
    )

    prompt = "\n\n".join(parts) + (
        "\n\nWrite a concise, natural-language answer to the question, grounded only in the "
        "data and context above - never invent a number or claim that isn't present in them. "
        "If the SQL result and the retrieved context point in different directions, say so "
        "explicitly rather than silently picking one. If the retrieved context is empty or "
        "clearly irrelevant, say honestly that there's no note or enablement content covering "
        "this rather than answering from the SQL result alone as if that were the full picture."
        + multi_account_note
    )

    stream = _get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": "You are a GTM data analyst writing one grounded answer from a SQL "
                "result and/or retrieved account notes and enablement content.",
            },
            {"role": "user", "content": prompt},
        ],
        stream=True,
        stream_options={"include_usage": True},
    )
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield ("delta", chunk.choices[0].delta.content)
        # The final chunk of a stream_options={"include_usage": True} stream
        # carries usage and an empty choices list - not a per-token delta.
        if chunk.usage:
            usage = {
                "prompt_tokens": chunk.usage.prompt_tokens,
                "completion_tokens": chunk.usage.completion_tokens,
            }
    yield ("usage", usage)


def _explain_sql_stream(question: str, sql_result: dict, history: list, memories: list = None):
    """
    A short, plain-language note attached to every "sql" answer -
    generated and verified-match alike - not RAG-grounded (no retrieval
    happens for a pure sql turn), just a read of the rows: what they show,
    any pattern worth calling out. Deliberately kept small (max_tokens
    caps it, not just the prompt), since this runs on every sql turn.

    Verified matches still skip the expensive part (the classification +
    SQL-generation call) - that's the actual optimization, and it stays
    $0. This explanation is generated live even for a verified match,
    though, rather than reusing a canned string written for the stored
    question: verified matching is fuzzy (similarity-based, not exact
    text), so a cached explanation could easily not match what was
    actually asked this time.
    """
    preview = sql_result["rows"][:10]
    parts = [
        f"Question: {question}",
        f"SQL result - columns: {sql_result['columns']}\nrows (first 10 of {len(sql_result['rows'])}): {preview}",
    ]
    history_text = _format_history(history)
    if history_text:
        parts.append(history_text)
    if memories:
        parts.append("What you remember about the person asking:\n" + _format_memories(memories))

    prompt = "\n\n".join(parts) + (
        "\n\nIn no more than two short sentences, explain what this result shows in plain "
        "language - call out a notable pattern, concentration, or standout value if there "
        "genuinely is one. Don't just restate the numbers, and don't invent context that isn't "
        "in the data above."
    )

    stream = _get_client().chat.completions.create(
        model=MODEL,
        messages=[
            {
                "role": "system",
                "content": "You are a GTM data analyst adding one brief, plain-language note "
                "under a SQL result - not a full answer, a short aside that helps someone "
                "learn what the numbers mean.",
            },
            {"role": "user", "content": prompt},
        ],
        stream=True,
        stream_options={"include_usage": True},
        max_tokens=120,
    )
    usage = {"prompt_tokens": 0, "completion_tokens": 0}
    for chunk in stream:
        if chunk.choices and chunk.choices[0].delta and chunk.choices[0].delta.content:
            yield ("delta", chunk.choices[0].delta.content)
        if chunk.usage:
            usage = {
                "prompt_tokens": chunk.usage.prompt_tokens,
                "completion_tokens": chunk.usage.completion_tokens,
            }
    yield ("usage", usage)


def _format_glossary(entries) -> str:
    if not entries:
        return "(no relevant business terms found)"
    return "\n".join(f"- {e['term']}: {e['definition']}" for e in entries)


def _format_history(history) -> str:
    if not history:
        return ""
    turns = []
    for turn in history:
        if turn["type"] == "sql":
            turns.append(
                f"Q: {turn['question']}\nSQL: {turn['sql']}\n"
                f"(returned {turn['row_count']} row(s); columns: {', '.join(turn['columns'])})"
            )
        elif turn["type"] == "hybrid":
            turns.append(
                f"Q: {turn['question']}\nSQL: {turn['sql']}\n"
                f"(returned {turn['row_count']} row(s); columns: {', '.join(turn['columns'])})\n"
                f"A: {turn['message']}"
            )
        else:
            turns.append(f"Q: {turn['question']}\nA: {turn['message']}")
    return (
        "\n\nPrevious turns in this conversation - use these to resolve follow-ups "
        "(\"that\", \"those\", \"now filter by...\") and to answer meta-questions about "
        "what you just said (\"are you sure?\", \"why?\", \"what does that mean?\"):\n\n"
        + "\n\n".join(turns)
    )


def _generate(system_prompt: str, messages: list):
    response = _get_client().chat.completions.parse(
        model=MODEL,
        messages=[{"role": "system", "content": system_prompt}] + messages,
        response_format=AgentResponse,
    )
    parsed = response.choices[0].message.parsed
    usage = {
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
    }
    return parsed, usage


def _build_system_prompt(
    tables, check_values, most_recent_date, context, join_paths, history, memories=None, user_id=None
) -> str:
    """
    The single classification + generation prompt behind every "sql" /
    "unstructured" / "hybrid" / "chat" decision run_ask_stream makes -
    pulled out into its own function so that ~450-line pipeline isn't
    broken up by a wall of prompt text sitting in the middle of it.
    """
    join_hint = ""
    if join_paths:
        join_hint = "\n\nRelevant table relationships (use these exact join paths):\n" + "\n".join(
            f"- {p}" for p in join_paths
        )

    # Only described to the model when it's really there - otherwise a
    # "do you remember me?" would get a confident yes from a deployment
    # with no memory store behind it.
    memory_about = ""
    memory_block = ""
    if memories is not None:
        memory_about = (
            " Long-term memory (HydraDB): what a user says about themselves - who they are, "
            "which accounts or regions they cover, how they like answers - is remembered "
            "across conversations, separately for each user, and recalled when relevant."
        )
        # The name they're asking under is known before anything has been
        # remembered - without it, a first "which of my accounts..." from
        # someone who had already said who they are (in the one place the
        # app asks) got "could you tell me your name?" back. Truncated:
        # it's free text from the client going into a prompt.
        identity = (
            f"\n\nThe person asking has identified themselves as: \"{user_id[:120]}\". Treat that "
            "as who they are - if it's a name, \"my accounts\" means the accounts where "
            "account_team.name ILIKE that name; if it also states a role or a preference, honour it."
            if user_id
            else ""
        )
        memory_block = (
            identity
            + "\n\nWhat you remember about this user from earlier conversations:\n"
            + (_format_memories(memories) or "(nothing relevant to this question yet)")
            + "\nUse it, with who they are above, to resolve first-person references and to "
            "honour stated preferences. Only if a first-person question can't be resolved from "
            "any of that or the conversation, ask in chat mode instead of guessing.\n"
            "Independently of the response mode: if this message states something durable about "
            "the user themselves - who they are, their role, which accounts, regions, industries "
            "or workloads they cover, how they want answers presented - restate it in user_fact "
            "as one short third-person sentence (\"The user is Derek Reyes, a CSM.\", \"The user "
            "wants answers as three bullets or fewer.\"). It is saved to their long-term memory, "
            "so acknowledge it briefly if the message was only that. Leave user_fact null "
            "otherwise: a question is never a fact about the user, and neither who they have "
            "identified themselves as nor anything already remembered above is new - never "
            "restate those."
        )

    return f"""You are a friendly, sharp GTM data analyst assistant, covering accounts,
deals, capacity contracts, and consumption for a consumption-based business
(commitments are purchased as capacity, then drawn down via usage - not
seat-based subscriptions).
You have four response modes, chosen per turn:

- "sql": the question asks for a number or a list this schema answers
  directly, with no "why" attached (e.g. "how many accounts are
  at_risk"). Write a single Postgres SELECT query in sql - no markdown
  fences, no explanation. Leave message and account_hints empty.
- "unstructured": the question asks for something only account notes or
  enablement content would have - a reason, a recommendation, a sales
  play, an objection-handling script - not a number (e.g. "how should I
  handle a pricing objection," "why did we lose this account"). No SQL.
  If the question names one or more specific accounts, set account_hints
  to every one of those account names exactly as mentioned in the
  question (a list - one item for a single account, several for a
  question comparing multiple accounts); leave it empty for a general,
  company-wide question. Leave sql and message empty - message gets
  filled in after retrieval, not by you here.
- "hybrid": the question needs both a real number from the schema AND the
  narrative behind it - most often "why is X declining / at risk /
  growing," including when it names several accounts at once ("why are X
  and Y both declining," "compare consumption trends across these 3
  accounts"). Write the SQL that gets the number(s) in sql - for more
  than one account, that means the SQL itself covers all of them, not
  just the first - AND set account_hints the same way as "unstructured,"
  listing every account name mentioned. Leave message empty.
- "chat": everything else - greetings, small talk, meta-questions about a
  previous answer ("are you sure?", "why did you write it that way?",
  "what does that mean?"), a genuine question about how this tool itself
  works (answer using the "About Traceview" section below, not a guess), a
  clarifying question you need to ask back because the request is
  ambiguous, or an honest decline when nothing in the schema or the notes
  can answer it (forecasts, satisfaction scores, external market data,
  etc). Write a natural, concise, warm reply in message - leave sql and
  account_hints empty. When explaining a previous answer, how the tool
  works, or declining a question, be specific about why, using the
  schema, the "About Traceview" section, and conversation history below -
  never your own general knowledge about what a similar-sounding term
  usually means elsewhere.

Never force a pure "sql" answer onto a question that's really asking
"why" - a number without its cause often isn't actually answering what
was asked. But also don't reach for "unstructured"/"hybrid" on a plain
factual lookup just because an account is mentioned - those modes cost a
second model call, so use them only when the question genuinely needs
narrative context, not for every question that happens to name an account.

Never guess or approximate a data answer with unrelated columns just to
have something in sql - an honest chat reply is correct; a plausible-
looking wrong query is not.

DO:
- Use the exact glossary definition below for a business term when the
  question uses it ("win rate", "active deal", "under-consumption", "NRR",
  etc.) - never infer your own definition for a term that's already
  defined. If a definition says to derive something from one column
  ("derive X from the trend, not from Y alone"), your SQL must actually
  compute that, not substitute the column it explicitly told you not to
  rely on just because it's simpler to filter on. Confirmed in testing:
  under-consumption's definition explicitly says not to use
  capacity_contracts.status alone, and a query that did anyway - instead
  of computing the actual trailing-consumption ratio - was wrong despite
  having the right definition available.
- Ground relative time phrases ("recently", "this year", "this quarter")
  in {most_recent_date}, the data's own most recent date - not today's
  real-world date, this is a static demo dataset, not a live feed.
- Use the join-path hints below exactly as given when a question spans
  more than one table.
- When filtering on a name-like text column (accounts.name, partners.name,
  workloads.name) by something mentioned in the question, match with
  ILIKE '%...%', never plain '='. People shorten and misspell company
  names ("Highfield Care" for "Highfield Care Partners") - an exact match
  against the literal words in the question silently returns zero rows
  instead of erroring, which looks like a real, if boring, answer rather
  than a failed lookup. ILIKE with wildcards still matches only the
  intended row when the exact name is given (it's a superset of '=', not
  a looser replacement for it), so there's no precision lost by always
  using it here.

DON'T:
- Don't confuse deals.deal_value with capacity_contracts.committed_amount
  - a deal is the negotiated opportunity, the contract is what actually
  gets created once it closes won, and they're rarely the exact same
  number. A "how much did we sell" question after close means the
  contract, not the deal.
- Don't join the activities table into a query unless the question is
  specifically about interactions, calls, POCs, or touchpoints - most
  deal/revenue questions don't need it.
- Don't assume close_date IS NOT NULL means a deal was won - a
  closed_lost deal also has a close_date.
- Don't invent a numeric threshold ("large deal", "high consumption") that
  isn't defined in the glossary - decline in chat mode and ask what
  threshold to use instead of guessing one.
- Don't pick a plausible-sounding but ungrounded interpretation of a term
  that could reasonably mean something else, and answer confidently as if
  it were the only meaning. Confirmed in testing: asked "how does the RAG
  component work," the model answered as if RAG meant a red/amber/green
  status indicator (a real concept elsewhere, but not anything this app
  has) instead of retrieval-augmented generation (this app's own
  retrieval pipeline, described below) - a fluent, wrong answer is worse
  than asking which one was meant, or saying the schema/notes/"About
  Traceview" section below don't cover it.

About Traceview (for genuine questions about how this tool itself works -
not the GTM data it answers questions about): retrieval-augmented
generation (RAG) - pulling relevant account notes or enablement content
via pgvector similarity search - runs for "unstructured"/"hybrid"
questions that need qualitative context, not for "sql" questions. A
knowledge graph computes real join paths from the schema's actual foreign
keys before SQL gets written, instead of the model guessing how tables
connect. A verified-query cache skips the classification and SQL-writing
step (at $0 cost) for questions matching one already vetted by the eval
suite - the result still gets a fresh explanation, since a cached one
could mismatch the exact question actually asked. Every step above is
timed and shown in the reasoning trace under each answer.{memory_about}
Answer questions about this architecture using only what's written here -
don't invent implementation details beyond it.

Schema:
{_format_schema(tables, check_values)}

Relevant business term definitions:
{_format_glossary(context)}
{join_hint}{memory_block}
{_format_history(history)}"""


def run_ask_stream(question: str, history: list = None, user_id: str = None):
    """
    The actual pipeline - a generator so the "unstructured"/"hybrid" paths
    can stream their synthesized answer token by token instead of
    blocking until the whole thing is written. Every branch ends by
    yielding exactly one {"type": "complete", "data": {...}} event, with
    the same shape run_ask() used to return outright - run_ask() below is
    just a thin wrapper that drains this generator and hands back that
    one dict, so eval and any other non-streaming caller sees identical
    behavior to before.

    "sql"/"chat"/verified-match turns have no writing step to stream, so
    they go straight to a single "complete" event. "unstructured"/"hybrid"
    first yield "start" (everything known so far, message still empty),
    then "delta" events as the answer is written, then "complete".
    """
    trace = []
    total_prompt_tokens = 0
    total_completion_tokens = 0

    # Everything the brain adds to a response on top of the answer itself
    # - spread into every "start"/"complete" payload below, so each branch
    # reports the same fields whether or not it touched HydraDB.
    brain = {"memories": [], "graph_paths": [], "retrieval_backend": None, "hydra_request_id": None, "remembered": None}
    memory_on = _memory_enabled(user_id)
    memories = None

    # Verified answers are context-free canned SQL - only safe to use on a
    # fresh question. Mid-conversation, the same words can mean something
    # scoped by prior turns ("what about win rate" after discussing one
    # department), and skipping straight to a verified generic answer
    # would silently ignore that context.
    #
    # Same reasoning for a first-person question when there's a memory to
    # consult: "which of my accounts are at risk" must not be served the
    # verified answer to "which accounts are at risk".
    personal = memory_on and bool(_FIRST_PERSON.search(question))
    t_verify = time.perf_counter()
    match = find_verified_match(question) if not history and not personal else None
    if match:
        columns, rows, exec_latency, truncated = run_select(match["sql"])
        trace.append({
            "step": "verified_match",
            "latency_ms": round((time.perf_counter() - t_verify) * 1000, 2),
            "execute_latency_ms": exec_latency,
            "matched_question": match["question"],
            "similarity": round(match["score"], 4),
        })

        yield {"type": "start", "data": {
            "response_type": "sql",
            "question": question,
            "generated_sql": match["sql"],
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "retrieved_context": [],
            "retrieved_sources": [],
            "trace": list(trace),
            **brain,
        }}

        # The SQL is safe to reuse verbatim - a verified match means this
        # exact query correctly answers this class of question, regardless
        # of phrasing. The explanation isn't: verified matching is fuzzy
        # (similarity >= MATCH_THRESHOLD, not exact text), so a canned
        # explanation written for the stored question could easily not
        # match what was actually asked. Generating it live, from the real
        # question and the real rows, is the only way to keep it honest -
        # this is the one part of the verified fast path that isn't free.
        t_explain = time.perf_counter()
        explanation_parts = []
        explain_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        explain_status = "sql"
        try:
            for kind, value in _explain_sql_stream(
                question, sql_result={"columns": columns, "rows": rows}, history=history
            ):
                if kind == "delta":
                    explanation_parts.append(value)
                    yield {"type": "delta", "text": value}
                else:
                    explain_usage = value
            explanation = "".join(explanation_parts)
        except Exception:
            # Same policy as the generated-sql path: the table above is
            # already correct - degrade to no explanation, never to a
            # fallback that might not match what was actually asked.
            explanation = None
            explain_status = "error"
        total_prompt_tokens += explain_usage["prompt_tokens"]
        total_completion_tokens += explain_usage["completion_tokens"]
        trace.append({
            "step": "explain_sql",
            "attempt": 1,
            "generate_latency_ms": round((time.perf_counter() - t_explain) * 1000, 2),
            "tokens": explain_usage,
            "status": explain_status,
        })

        estimated_cost = estimate_cost_usd(MODEL, total_prompt_tokens, total_completion_tokens)
        yield {"type": "complete", "data": {
            "response_type": "sql",
            "question": question,
            "message": None,
            "explanation": explanation,
            "generated_sql": match["sql"],
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "attempts": 1,
            "verified": True,
            "retrieved_context": [],
            "retrieved_sources": [],
            "trace": trace,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "estimated_cost_usd": estimated_cost,
            **brain,
        }}
        return

    memory_future = _memory_pool.submit(_recall_memories, user_id, question) if memory_on else None

    t0 = time.perf_counter()
    context = retrieve_glossary(question, top_k=3)
    trace.append({
        "step": "retrieve",
        "latency_ms": round((time.perf_counter() - t0) * 1000, 2),
        "retrieved_terms": [c["term"] for c in context],
        "history_turns_used": len(history) if history else 0,
    })

    t_graph = time.perf_counter()
    relevant_entities = find_relevant_entities(question)
    join_paths = find_join_paths(relevant_entities)
    trace.append({
        "step": "graph_lookup",
        "latency_ms": round((time.perf_counter() - t_graph) * 1000, 2),
        "relevant_entities": relevant_entities,
        "join_paths": join_paths,
    })

    with get_connection() as conn:
        tables = fetch_schema(conn)
        check_values = fetch_check_constraint_values(conn)
        most_recent_date = _most_recent_data_date(conn)

    if memory_future is not None:
        memories, memory_step = memory_future.result()
        trace.append(memory_step)
        brain["memories"] = memories

    system_prompt = _build_system_prompt(
        tables, check_values, most_recent_date, context, join_paths, history, memories=memories, user_id=user_id
    )

    messages = [{"role": "user", "content": question}]
    last_error = None

    for attempt in range(1, MAX_ATTEMPTS + 1):
        t1 = time.perf_counter()
        parsed, usage = _generate(system_prompt, messages)
        gen_latency = round((time.perf_counter() - t1) * 1000, 2)
        total_prompt_tokens += usage["prompt_tokens"]
        total_completion_tokens += usage["completion_tokens"]

        # Reported back rather than written here - main.py does the actual
        # save once the turn has completed, the same place it persists the
        # conversation turn, so a turn that errors out remembers nothing.
        if memory_on and parsed.user_fact and parsed.user_fact.strip():
            brain["remembered"] = parsed.user_fact.strip()

        if parsed.response_type == "chat":
            trace.append({
                "step": "respond",
                "attempt": attempt,
                "generate_latency_ms": gen_latency,
                "tokens": usage,
                "status": "chat",
            })
            estimated_cost = estimate_cost_usd(MODEL, total_prompt_tokens, total_completion_tokens)
            yield {"type": "complete", "data": {
                "response_type": "chat",
                "question": question,
                "message": parsed.message,
                "generated_sql": None,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "attempts": attempt,
                "verified": False,
                "retrieved_context": context,
                "retrieved_sources": [],
                "trace": trace,
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "estimated_cost_usd": estimated_cost,
                **brain,
            }}
            return

        if parsed.response_type == "unstructured":
            retrieved, account_names = _retrieve_account_context(question, parsed.account_hints, attempt, trace)
            sources = retrieved["sources"]
            brain.update(
                graph_paths=retrieved["graph_paths"],
                retrieval_backend=retrieved["backend"],
                hydra_request_id=retrieved["hydra_request_id"],
            )

            yield {"type": "start", "data": {
                "response_type": "unstructured",
                "question": question,
                "generated_sql": None,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "retrieved_context": context,
                "retrieved_sources": sources,
                "trace": list(trace),
                **brain,
            }}

            t_synth = time.perf_counter()
            message_parts = []
            synth_usage = {"prompt_tokens": 0, "completion_tokens": 0}
            synth_status = "unstructured"
            try:
                for kind, value in _synthesize_stream(
                    question,
                    sql_result=None,
                    sources=sources,
                    history=history,
                    account_names=account_names,
                    graph_paths=retrieved["graph_paths"],
                    memories=memories,
                ):
                    if kind == "delta":
                        message_parts.append(value)
                        yield {"type": "delta", "text": value}
                    else:
                        synth_usage = value
                message = "".join(message_parts)
            except Exception as e:
                # Retrieval already succeeded - losing that over a second
                # model call failing would be worse than an honest, if
                # unpolished, fallback. Whatever text already streamed is
                # discarded (not silently kept half-written) - the frontend
                # replaces it with this fallback on a "synth_error" event.
                message = (
                    "I found relevant context but hit an error writing the summary "
                    "(" + type(e).__name__ + "). Try asking again."
                )
                synth_usage = {"prompt_tokens": 0, "completion_tokens": 0}
                synth_status = "error"
                yield {"type": "synth_error", "text": message}
            total_prompt_tokens += synth_usage["prompt_tokens"]
            total_completion_tokens += synth_usage["completion_tokens"]
            trace.append({
                "step": "synthesize",
                "attempt": attempt,
                "generate_latency_ms": round((time.perf_counter() - t_synth) * 1000, 2),
                "tokens": synth_usage,
                "status": synth_status,
            })

            estimated_cost = estimate_cost_usd(MODEL, total_prompt_tokens, total_completion_tokens)
            yield {"type": "complete", "data": {
                "response_type": "unstructured",
                "question": question,
                "message": message,
                "generated_sql": None,
                "columns": [],
                "rows": [],
                "row_count": 0,
                "truncated": False,
                "attempts": attempt,
                "verified": False,
                "retrieved_context": context,
                "retrieved_sources": sources,
                "trace": trace,
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "estimated_cost_usd": estimated_cost,
                **brain,
            }}
            return

        # "sql" and "hybrid" both execute a query first, sharing the same
        # retry-on-error handling - they only diverge after it succeeds.
        sql = parsed.sql.strip()
        messages.append({"role": "assistant", "content": sql})

        try:
            columns, rows, exec_latency, truncated = run_select(sql)
        except QueryError as e:
            last_error = str(e)
            trace.append({
                "step": "respond",
                "attempt": attempt,
                "sql": sql,
                "generate_latency_ms": gen_latency,
                "tokens": usage,
                "status": "error",
                "error": last_error,
            })
            messages.append({
                "role": "user",
                "content": f"That query failed with error: {last_error}. Fix it and return only the corrected SQL.",
            })
            continue

        trace.append({
            "step": "respond",
            "attempt": attempt,
            "sql": sql,
            "generate_latency_ms": gen_latency,
            "execute_latency_ms": exec_latency,
            "tokens": usage,
            "status": "success",
            "row_count": len(rows),
            "truncated": truncated,
        })

        if parsed.response_type == "sql":
            yield {"type": "start", "data": {
                "response_type": "sql",
                "question": question,
                "generated_sql": sql,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
                "retrieved_context": context,
                "retrieved_sources": [],
                "trace": list(trace),
                **brain,
            }}

            # Every "sql" turn gets a short explanatory note, generated
            # live from the actual rows - including verified matches (see
            # the verified-match branch above): only the SQL is safe to
            # reuse verbatim, not a canned explanation for it.
            t_explain = time.perf_counter()
            explanation_parts = []
            explain_usage = {"prompt_tokens": 0, "completion_tokens": 0}
            explain_status = "sql"
            try:
                for kind, value in _explain_sql_stream(
                    question, sql_result={"columns": columns, "rows": rows}, history=history, memories=memories
                ):
                    if kind == "delta":
                        explanation_parts.append(value)
                        yield {"type": "delta", "text": value}
                    else:
                        explain_usage = value
                explanation = "".join(explanation_parts)
            except Exception:
                # The table above is already correct and complete - a
                # failed explanation is a lost bonus, not a failed turn,
                # so this fails silently rather than showing an error
                # note under an otherwise-fine result.
                explanation = None
                explain_status = "error"
            total_prompt_tokens += explain_usage["prompt_tokens"]
            total_completion_tokens += explain_usage["completion_tokens"]
            trace.append({
                "step": "explain_sql",
                "attempt": attempt,
                "generate_latency_ms": round((time.perf_counter() - t_explain) * 1000, 2),
                "tokens": explain_usage,
                "status": explain_status,
            })

            estimated_cost = estimate_cost_usd(MODEL, total_prompt_tokens, total_completion_tokens)
            yield {"type": "complete", "data": {
                "response_type": "sql",
                "question": question,
                "message": None,
                "explanation": explanation,
                "generated_sql": sql,
                "columns": columns,
                "rows": rows,
                "row_count": len(rows),
                "truncated": truncated,
                "attempts": attempt,
                "verified": False,
                "retrieved_context": context,
                "retrieved_sources": [],
                "trace": trace,
                "prompt_tokens": total_prompt_tokens,
                "completion_tokens": total_completion_tokens,
                "estimated_cost_usd": estimated_cost,
                **brain,
            }}
            return

        # hybrid - the SQL succeeded above; now pull unstructured context
        # for the same account(s) and synthesize one answer from both.
        account_hints = parsed.account_hints or _account_hints_from_rows(columns, rows)
        retrieved, account_names = _retrieve_account_context(question, account_hints, attempt, trace)
        sources = retrieved["sources"]
        brain.update(
            graph_paths=retrieved["graph_paths"],
            retrieval_backend=retrieved["backend"],
            hydra_request_id=retrieved["hydra_request_id"],
        )

        yield {"type": "start", "data": {
            "response_type": "hybrid",
            "question": question,
            "generated_sql": sql,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "retrieved_context": context,
            "retrieved_sources": sources,
            "trace": list(trace),
            **brain,
        }}

        t_synth = time.perf_counter()
        message_parts = []
        synth_usage = {"prompt_tokens": 0, "completion_tokens": 0}
        synth_status = "hybrid"
        try:
            for kind, value in _synthesize_stream(
                question,
                sql_result={"columns": columns, "rows": rows},
                sources=sources,
                history=history,
                account_names=account_names,
                graph_paths=retrieved["graph_paths"],
                memories=memories,
            ):
                if kind == "delta":
                    message_parts.append(value)
                    yield {"type": "delta", "text": value}
                else:
                    synth_usage = value
            message = "".join(message_parts)
        except Exception as e:
            # The SQL already succeeded - this turn still returns real,
            # correct data below even if the written summary fails.
            message = (
                "The query results below are correct, but I hit an error writing the summary "
                "that combines them with account context (" + type(e).__name__ + "). Try asking again."
            )
            synth_usage = {"prompt_tokens": 0, "completion_tokens": 0}
            synth_status = "error"
            yield {"type": "synth_error", "text": message}
        total_prompt_tokens += synth_usage["prompt_tokens"]
        total_completion_tokens += synth_usage["completion_tokens"]
        trace.append({
            "step": "synthesize",
            "attempt": attempt,
            "generate_latency_ms": round((time.perf_counter() - t_synth) * 1000, 2),
            "tokens": synth_usage,
            "status": synth_status,
        })

        estimated_cost = estimate_cost_usd(MODEL, total_prompt_tokens, total_completion_tokens)
        yield {"type": "complete", "data": {
            "response_type": "hybrid",
            "question": question,
            "message": message,
            "generated_sql": sql,
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "truncated": truncated,
            "attempts": attempt,
            "verified": False,
            "retrieved_context": context,
            "retrieved_sources": sources,
            "trace": trace,
            "prompt_tokens": total_prompt_tokens,
            "completion_tokens": total_completion_tokens,
            "estimated_cost_usd": estimated_cost,
            **brain,
        }}
        return

    raise AgentError(
        f"Agent failed after {MAX_ATTEMPTS} attempts. Last error: {last_error}",
        prompt_tokens=total_prompt_tokens,
        completion_tokens=total_completion_tokens,
    )


def run_ask(question: str, history: list = None, user_id: str = None) -> dict:
    """
    Synchronous entry point - drains run_ask_stream() and hands back only
    its final "complete" event's data, discarding the "start"/"delta"
    events along the way. Used by eval and anywhere else that just wants
    the finished answer, not the token-by-token stream; behavior is
    identical to the old non-streaming run_ask(), since this runs the
    exact same code path, just without a consumer for the deltas.
    """
    result = None
    for event in run_ask_stream(question, history=history, user_id=user_id):
        if event["type"] == "complete":
            result = event["data"]
    return result
