"""
LLM-as-judge: scores whether generated SQL actually answers the question,
not just whether it looks plausible.

Pattern/row-count checks in run_eval.py catch obviously wrong SQL, but
they're a weak proxy - a query can contain the right keyword and return
the right number of rows while still answering the wrong question (wrong
join direction, a missing filter, inverted logic). The judge is a second,
independent model call that sees the question, the SQL, and the actual
result, and verifies the semantics - the same technique used to eval LLM
outputs across the industry, not something bespoke to this project.

Runs on a separate, cheaper model by default (OPENAI_JUDGE_MODEL) - the
judge's job is narrow (verify one query against one question), so it
doesn't need the same model tier as generation. Using a cheaper model for
verification instead of defaulting everything to the expensive one is a
deliberate cost decision, not an oversight.
"""

import os

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

JUDGE_MODEL = os.environ.get("OPENAI_JUDGE_MODEL", "gpt-4o-mini")

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI()
    return _client


class JudgeVerdict(BaseModel):
    correct: bool
    reasoning: str


def judge_sql_answer(
    question: str,
    sql: str,
    columns: list,
    rows: list,
    glossary_context: list = None,
    history: list = None,
) -> JudgeVerdict:
    """
    glossary_context and history must be the SAME grounding the agent had
    when it wrote the SQL (agent.run_ask()'s retrieved_context and the
    conversation history passed in) - a judge given less context than the
    generator will flag context-dependent correct answers as wrong. This
    isn't a hypothetical: without glossary_context, this judge failed
    "active deals" (correctly filtered to prospecting/negotiation per the
    project's own glossary) because it didn't know that definition existed.
    """
    preview_rows = rows[:5]

    glossary_text = "(none retrieved)"
    if glossary_context:
        glossary_text = "\n".join(f"- {c['term']}: {c['definition']}" for c in glossary_context)

    history_text = "(none - this is the first turn)"
    if history:
        parts = []
        for turn in history:
            if turn["type"] == "sql":
                parts.append(f"Q: {turn['question']}\nSQL: {turn['sql']}")
            else:
                parts.append(f"Q: {turn['question']}\nA: {turn['message']}")
        history_text = "\n\n".join(parts)

    prompt = f"""Question: {question}

Business term definitions the agent had access to (use these to judge
whether it correctly applied project-specific meanings, e.g. "active deal"
has a specific definition here, not a generic one):
{glossary_text}

Prior conversation turns the agent had access to (relevant if the question
uses "that", "those", or otherwise refers back to a previous turn):
{history_text}

SQL query the agent wrote:
{sql}

Result columns: {columns}
Result rows (first 5): {preview_rows}

Does this SQL query correctly answer the question, given the definitions
and conversation context above? A query can look plausible and still be
wrong (e.g. counting all deals when the question asked only about closed
ones, or joining the wrong foreign key) - but it can also look
"incomplete" to someone without this context while actually being exactly
right (e.g. filtering to specific stages because that's this project's
definition of "active"). Judge using the context given, not generic
assumptions about what these terms usually mean elsewhere.

One specific reasoning trap to avoid: an INNER JOIN on a nullable foreign
key already excludes every row where that key is NULL - SQL's NULL is
never equal to anything, including another NULL, so the join condition
itself does the filtering. Don't flag a query as missing a "WHERE x IS
NOT NULL" check when an INNER JOIN on that same column already guarantees
it structurally; that's a false positive, not a real gap. Only flag a
missing NULL filter if the join is a LEFT/RIGHT JOIN, which would let
NULLs through.

The broader version of that trap: don't invent an additional filter or
condition the query "should" have applied unless the question itself, or
one of the definitions above, actually asked for it. A query that answers
exactly the question asked - nothing more scoped, nothing more filtered -
is correct, even if a *different*, more specific question could have been
asked instead. Confirmed in practice: this judge has flagged correct,
unscoped queries as wrong for not filtering by a status or a stage that
nothing in the question or the definitions ever mentioned. If you notice
yourself about to require a condition, first check it traces back to
actual text above - not to what would generically seem more thorough.

Set correct to true only if you're confident the logic is right; briefly
explain your reasoning."""

    response = _get_client().chat.completions.parse(
        model=JUDGE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format=JudgeVerdict,
        # Confirmed in practice: with no temperature set (defaulting to
        # 1.0), this judge flip-flopped on the identical SQL for the
        # identical question across two eval runs, minutes apart, with no
        # code change in between - a correctness verdict shouldn't depend
        # on sampling luck the way generation's own wording can. 0 isn't a
        # full determinism guarantee at the API level, but it's the right
        # direction for a task that's judging right/wrong, not writing.
        temperature=0,
    )
    return response.choices[0].message.parsed


class FaithfulnessVerdict(BaseModel):
    faithful: bool
    reasoning: str


def judge_hybrid_faithfulness(question: str, sql_columns: list, sql_rows: list, sources: list, message: str):
    """
    A different question from judge_sql_answer: not "was the right SQL/
    retrieval chosen" (routing accuracy's job), but "does the synthesized
    prose actually say only what the SQL result and retrieved context
    support." This is the failure mode unique to hybrid synthesis - a
    fluent answer that quietly states a number that isn't in the result,
    or resolves a disagreement between the two sources by picking one
    without saying so.
    """
    preview_rows = sql_rows[:10] if sql_rows else []
    source_text = (
        "\n\n".join(f"[{s['source_type']}] {s['text']}" for s in sources) if sources else "(none retrieved)"
    )

    prompt = f"""Question: {question}

SQL result - columns: {sql_columns}
rows (first 10): {preview_rows}

Retrieved context:
{source_text}

Answer given to the user:
{message}

Does this answer faithfully reflect ONLY what's in the SQL result and retrieved context above -
no invented numbers, no claims not backed by either source, and no silently resolving a genuine
disagreement between them by picking one side without saying so? An answer can be faithful even
if it's incomplete or doesn't use every row returned; it's unfaithful if it states something as
fact that the data and context above don't actually support. Set faithful to true only if
you're confident; briefly explain your reasoning."""

    response = _get_client().chat.completions.parse(
        model=JUDGE_MODEL,
        messages=[{"role": "user", "content": prompt}],
        response_format=FaithfulnessVerdict,
        temperature=0,
    )
    return response.choices[0].message.parsed
