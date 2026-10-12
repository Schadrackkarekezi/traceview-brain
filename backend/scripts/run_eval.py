"""
Evaluation harness for the /ask agent.

Four suites, run together:

  1. Single-turn (eval_set.json) - one question each, checked three ways:
     deterministic pattern/row-count checks (fast, free, catch obviously
     wrong SQL) AND an LLM-as-judge semantic check (judge.py - catches SQL
     that looks plausible but answers the wrong question). For "ambiguous"
     questions, a pass means the agent replied in chat mode instead of
     hallucinating a query the data can't support.

  2. Multi-turn (eval_conversations.json) - sequences of questions run
     through run_ask() with accumulating history, exactly like a real
     conversation. Regression-tests follow-up resolution ("now break that
     down by department") instead of relying on a single manual spot-check.

  3. Retrieval hit-rate (eval_retrieval.json) - checks retrieve_unstructured()
     directly, no LLM generation involved, just "is the expected chunk in
     the top-k." Deliberately includes one documented known-miss rather
     than being curated to always show 100% - the point of tracking hit-
     rate is knowing the real number, and a suite tuned to never fail has
     no regression-detection value.

  4. Routing accuracy + hybrid faithfulness (eval_routing.json) - checks
     the router picked the right response_type (sql/unstructured/hybrid/
     chat), independent of whether the final answer is correct. For
     "hybrid" cases specifically, also runs judge_hybrid_faithfulness to
     catch the failure mode unique to synthesis: a fluent answer that
     contradicts or goes beyond what the SQL result and retrieved sources
     actually support. Routing correctness and faithfulness are tracked
     as separate flags, not conflated into one pass/fail - a
     correctly-routed-but-unfaithful case is a different problem than a
     misrouted one.

Upload isolation is NOT a fifth suite here - test_upload.py in pytest
already covers it properly (a deterministic security property, not
something that benefits from probabilistic LLM judgment), and duplicating
it into this LLM-cost-incurring framework would just pay for a judgment
call the property doesn't need.

A cost/latency budget check runs against eval_set's own numbers at the
end - thresholds set with real headroom above observed numbers, not
picked arbitrarily (see BUDGET_* below).

AgentError (retries exhausted on genuinely broken SQL) always counts as a
failure - that's a real bug, not a valid outcome for any eval case.

Usage: python -m scripts.run_eval   (run from backend/)
"""

import json
import time

from app.agent import run_ask, AgentError
from app.conversations import build_turn
from app.judge import judge_sql_answer, judge_hybrid_faithfulness
from app.retrieval import retrieve_unstructured

EVAL_SET_PATH = "eval/eval_set.json"
EVAL_CONVERSATIONS_PATH = "eval/eval_conversations.json"
EVAL_RETRIEVAL_PATH = "eval/eval_retrieval.json"
EVAL_ROUTING_PATH = "eval/eval_routing.json"
RESULTS_PATH = "eval_results.json"

# Calibrated from observed numbers (single-turn avg ~1.3-2.0s, hybrid's two
# calls costing ~$0.0085) with real headroom, not picked arbitrarily.
BUDGET_MAX_AVG_LATENCY_MS = 5000
BUDGET_MAX_AVG_COST_USD = 0.02


def check_sql_case(case: dict, result: dict, history: list = None) -> tuple[bool, str]:
    if result["response_type"] != "sql":
        return False, f"expected a SQL answer, agent replied in chat mode: {result['message']}"

    sql_lower = result["generated_sql"].lower()

    pattern = case.get("expected_sql_pattern")
    if pattern and pattern.lower() not in sql_lower:
        return False, f"expected SQL to contain '{pattern}', got: {result['generated_sql']}"

    expected_rows = case.get("expected_row_count")
    if expected_rows is not None and result["row_count"] != expected_rows:
        return False, f"expected {expected_rows} row(s), got {result['row_count']}"

    verdict = judge_sql_answer(
        case["question"],
        result["generated_sql"],
        result["columns"],
        result["rows"],
        glossary_context=result["retrieved_context"],
        history=history,
    )
    if not verdict.correct:
        return False, f"judge flagged as semantically wrong: {verdict.reasoning}"

    return True, f"ok - judge: {verdict.reasoning}"


def run_case(case: dict) -> dict:
    start = time.perf_counter()
    try:
        result = run_ask(case["question"])
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        cost = result.get("estimated_cost_usd", 0.0)

        if case.get("expect_failure"):
            passed = result["response_type"] == "chat"
            return {
                "question": case["question"],
                "category": case["category"],
                "passed": passed,
                "reason": "declined gracefully as expected" if passed
                else f"expected a chat decline, agent answered with: {result['generated_sql']}",
                "latency_ms": latency_ms,
                "estimated_cost_usd": cost,
                "attempts": result["attempts"],
            }

        passed, reason = check_sql_case(case, result)
        return {
            "question": case["question"],
            "category": case["category"],
            "passed": passed,
            "reason": reason,
            "generated_sql": result.get("generated_sql"),
            "row_count": result["row_count"],
            "attempts": result["attempts"],
            "latency_ms": latency_ms,
            "estimated_cost_usd": cost,
        }

    except AgentError as e:
        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        return {
            "question": case["question"],
            "category": case["category"],
            "passed": False,
            "reason": f"agent error: {e}",
            "latency_ms": latency_ms,
            "estimated_cost_usd": 0.0,
        }


def run_conversation_case(scenario: dict) -> dict:
    history = []
    turn_results = []
    all_passed = True

    for turn in scenario["turns"]:
        start = time.perf_counter()
        try:
            result = run_ask(turn["question"], history=history)
        except AgentError as e:
            all_passed = False
            turn_results.append({"question": turn["question"], "passed": False, "reason": f"agent error: {e}"})
            break

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        history_before_this_turn = list(history)  # same context the agent itself saw
        history.append(build_turn(turn["question"], result))

        if turn.get("expect_chat"):
            passed = result["response_type"] == "chat"
            reason = "ok" if passed else f"expected a chat reply, got SQL: {result.get('generated_sql')}"
        else:
            passed, reason = check_sql_case(turn, result, history=history_before_this_turn)

        if not passed:
            all_passed = False
        turn_results.append({
            "question": turn["question"],
            "passed": passed,
            "reason": reason,
            "latency_ms": latency_ms,
        })

    return {"name": scenario["name"], "passed": all_passed, "turns": turn_results}


def run_retrieval_case(case: dict) -> dict:
    results = retrieve_unstructured(case["question"], account_id=case.get("account_id"), top_k=8)

    if case["expected_source_type"] == "enablement_content":
        hit = any(
            r["source_type"] == "enablement_content" and _title_matches(r, case["expected_title"])
            for r in results
        )
        detail = case["expected_title"]
    else:  # account_note - checking the right account's own note surfaced, not a specific note ID
        hit = any(
            r["source_type"] == "account_note" and r["account_id"] == case["account_id"] for r in results
        )
        detail = f"account_note for account_id={case['account_id']}"

    known_limitation = case.get("known_limitation")
    return {
        "question": case["question"],
        "expected": detail,
        "hit": hit,
        "known_limitation": known_limitation,
        # A documented known-miss doesn't count against the headline hit-rate -
        # it's tracked, not hidden, but it can't "regress" below where it
        # already honestly sits.
        "counts_as_pass": hit or bool(known_limitation),
    }


def _title_matches(chunk_result: dict, expected_title: str) -> bool:
    from app.database import get_connection

    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT title FROM enablement_content WHERE content_id = %s", (chunk_result["source_id"],))
            row = cur.fetchone()
    return row is not None and row[0] == expected_title


def run_routing_case(case: dict) -> dict:
    start = time.perf_counter()
    try:
        result = run_ask(case["question"])
    except AgentError as e:
        return {
            "question": case["question"],
            "expected_response_type": case["expected_response_type"],
            "actual_response_type": None,
            "routing_correct": False,
            "faithful": None,
            "reason": f"agent error: {e}",
            "latency_ms": round((time.perf_counter() - start) * 1000, 2),
        }

    latency_ms = round((time.perf_counter() - start) * 1000, 2)
    routing_correct = result["response_type"] == case["expected_response_type"]

    faithful = None
    faithfulness_reason = ""
    if routing_correct and result["response_type"] == "hybrid":
        verdict = judge_hybrid_faithfulness(
            case["question"], result["columns"], result["rows"], result["retrieved_sources"], result["message"]
        )
        faithful = verdict.faithful
        faithfulness_reason = f" - faithfulness: {verdict.reasoning}"

    return {
        "question": case["question"],
        "expected_response_type": case["expected_response_type"],
        "actual_response_type": result["response_type"],
        "routing_correct": routing_correct,
        "faithful": faithful,
        "reason": (
            "ok" if routing_correct else f"expected {case['expected_response_type']}, got {result['response_type']}"
        ) + faithfulness_reason,
        "latency_ms": latency_ms,
    }


def main():
    with open(EVAL_SET_PATH) as f:
        eval_set = json.load(f)
    with open(EVAL_CONVERSATIONS_PATH) as f:
        conversations = json.load(f)
    with open(EVAL_RETRIEVAL_PATH) as f:
        retrieval_cases = json.load(f)
    with open(EVAL_ROUTING_PATH) as f:
        routing_cases = json.load(f)

    results = [run_case(case) for case in eval_set]
    conversation_results = [run_conversation_case(scenario) for scenario in conversations]
    retrieval_results = [run_retrieval_case(case) for case in retrieval_cases]
    routing_results = [run_routing_case(case) for case in routing_cases]

    total = len(results)
    passed = sum(r["passed"] for r in results)
    accuracy = round(100 * passed / total, 1)
    avg_latency = round(sum(r["latency_ms"] for r in results) / total, 1)
    avg_cost = round(sum(r["estimated_cost_usd"] for r in results) / total, 5)

    convo_total = len(conversation_results)
    convo_passed = sum(c["passed"] for c in conversation_results)

    retrieval_total = len(retrieval_results)
    retrieval_hits = sum(r["counts_as_pass"] for r in retrieval_results)
    retrieval_raw_hits = sum(r["hit"] for r in retrieval_results)  # not counting known-limitation passes

    routing_total = len(routing_results)
    routing_correct_count = sum(r["routing_correct"] for r in routing_results)
    faithfulness_checked = [r for r in routing_results if r["faithful"] is not None]
    faithful_count = sum(r["faithful"] for r in faithfulness_checked)

    print(f"\n{'=' * 60}")
    print(f"Traceview Agent Eval - {passed}/{total} single-turn passed ({accuracy}%)")
    print(f"Conversations - {convo_passed}/{convo_total} passed")
    print(f"Retrieval hit-rate - {retrieval_raw_hits}/{retrieval_total} raw "
          f"({retrieval_hits}/{retrieval_total} counting documented known limitations)")
    print(f"Routing accuracy - {routing_correct_count}/{routing_total}")
    if faithfulness_checked:
        print(f"Hybrid faithfulness - {faithful_count}/{len(faithfulness_checked)}")
    print(f"Average latency: {avg_latency} ms (budget: {BUDGET_MAX_AVG_LATENCY_MS} ms)")
    print(f"Average cost: ${avg_cost} (budget: ${BUDGET_MAX_AVG_COST_USD})")
    print(f"{'=' * 60}\n")

    by_category = {}
    for r in results:
        by_category.setdefault(r["category"], []).append(r)

    for category, cases in by_category.items():
        cat_passed = sum(c["passed"] for c in cases)
        print(f"[{category}] {cat_passed}/{len(cases)}")
        for c in cases:
            mark = "✅" if c["passed"] else "❌"
            print(f"  {mark} {c['question']}")
            if not c["passed"]:
                print(f"      -> {c['reason']}")
        print()

    print("[conversations]")
    for c in conversation_results:
        mark = "✅" if c["passed"] else "❌"
        print(f"  {mark} {c['name']}")
        for t in c["turns"]:
            tmark = "✅" if t["passed"] else "❌"
            print(f"      {tmark} {t['question']}")
            if not t["passed"]:
                print(f"          -> {t['reason']}")
    print()

    print("[retrieval hit-rate]")
    for r in retrieval_results:
        mark = "✅" if r["hit"] else ("🟡" if r["known_limitation"] else "❌")
        print(f"  {mark} {r['question']!r} -> {r['expected']}")
        if r["known_limitation"] and not r["hit"]:
            print(f"      (known limitation: {r['known_limitation']})")
    print()

    print("[routing accuracy + hybrid faithfulness]")
    for r in routing_results:
        mark = "✅" if r["routing_correct"] else "❌"
        faith_mark = ""
        if r["faithful"] is not None:
            faith_mark = "  [faithful ✅]" if r["faithful"] else "  [faithful ❌]"
        print(f"  {mark} {r['question']} (expected {r['expected_response_type']}, got {r['actual_response_type']}){faith_mark}")
        if not r["routing_correct"] or r["faithful"] is False:
            print(f"      -> {r['reason']}")
    print()

    budget_ok = avg_latency <= BUDGET_MAX_AVG_LATENCY_MS and avg_cost <= BUDGET_MAX_AVG_COST_USD
    print(f"[cost/latency budget] {'✅ within budget' if budget_ok else '❌ OVER BUDGET'}")
    print()

    with open(RESULTS_PATH, "w") as f:
        json.dump(
            {
                "accuracy": accuracy,
                "passed": passed,
                "total": total,
                "avg_latency_ms": avg_latency,
                "avg_cost_usd": avg_cost,
                "conversations_passed": convo_passed,
                "conversations_total": convo_total,
                "retrieval_hit_rate_raw": f"{retrieval_raw_hits}/{retrieval_total}",
                "retrieval_hit_rate_counting_known_limitations": f"{retrieval_hits}/{retrieval_total}",
                "routing_accuracy": f"{routing_correct_count}/{routing_total}",
                "hybrid_faithfulness": f"{faithful_count}/{len(faithfulness_checked)}" if faithfulness_checked else None,
                "budget_ok": budget_ok,
                "results": results,
                "conversation_results": conversation_results,
                "retrieval_results": retrieval_results,
                "routing_results": routing_results,
            },
            f,
            indent=2,
        )

    print(f"Full results saved to {RESULTS_PATH}")


if __name__ == "__main__":
    main()
