"""
conversations.py moved from an in-memory dict to a real conversation_turns
table specifically so history survives a server restart - these run
against the real table, not a mock, since a mock would happily pass even
if the actual SQL were wrong.
"""

import uuid

from app.conversations import add_turn, clear_conversation, get_history, resolve_conversation_id


def _sql_result(row_count=3):
    return {
        "response_type": "sql",
        "generated_sql": "SELECT * FROM accounts",
        "row_count": row_count,
        "columns": ["account_id", "name"],
    }


def _chat_result(message="Sure, here's what that means."):
    return {"response_type": "chat", "message": message}


def test_resolve_conversation_id_generates_one_when_none_given():
    assert resolve_conversation_id(None)
    assert resolve_conversation_id("")
    assert resolve_conversation_id("existing-id") == "existing-id"


def test_add_turn_and_get_history_round_trip():
    conv_id = str(uuid.uuid4())
    try:
        add_turn(conv_id, "How many accounts?", _sql_result(row_count=5))
        history = get_history(conv_id)
        assert len(history) == 1
        assert history[0]["type"] == "sql"
        assert history[0]["question"] == "How many accounts?"
        assert history[0]["row_count"] == 5
    finally:
        clear_conversation(conv_id)


def test_history_survives_a_fresh_connection():
    # The whole point of moving off in-memory storage - a brand new
    # get_history() call (a stand-in for "a different process entirely")
    # must still see turns written earlier, not just within one Python
    # process's lifetime.
    conv_id = str(uuid.uuid4())
    try:
        add_turn(conv_id, "How many accounts?", _sql_result())
        add_turn(conv_id, "What about at_risk ones?", _chat_result())
        history = get_history(conv_id)
        assert [t["question"] for t in history] == [
            "How many accounts?",
            "What about at_risk ones?",
        ]
    finally:
        clear_conversation(conv_id)


def test_get_history_only_returns_last_max_turns():
    from app.conversations import MAX_TURNS

    conv_id = str(uuid.uuid4())
    try:
        for i in range(MAX_TURNS + 3):
            add_turn(conv_id, f"question {i}", _chat_result(f"answer {i}"))
        history = get_history(conv_id)
        assert len(history) == MAX_TURNS
        # Still in chronological order, and it's the *most recent* ones kept.
        assert history[-1]["question"] == f"question {MAX_TURNS + 2}"
        assert history[0]["question"] == f"question {3}"
    finally:
        clear_conversation(conv_id)


def test_get_history_is_isolated_between_conversations():
    conv_a = str(uuid.uuid4())
    conv_b = str(uuid.uuid4())
    try:
        add_turn(conv_a, "question in A", _chat_result())
        add_turn(conv_b, "question in B", _chat_result())
        assert [t["question"] for t in get_history(conv_a)] == ["question in A"]
        assert [t["question"] for t in get_history(conv_b)] == ["question in B"]
    finally:
        clear_conversation(conv_a)
        clear_conversation(conv_b)


def test_clear_conversation_removes_all_its_turns():
    conv_id = str(uuid.uuid4())
    add_turn(conv_id, "question", _chat_result())
    assert len(get_history(conv_id)) == 1
    clear_conversation(conv_id)
    assert get_history(conv_id) == []


def test_hybrid_turn_keeps_both_sql_and_message():
    conv_id = str(uuid.uuid4())
    try:
        result = {
            "response_type": "hybrid",
            "generated_sql": "SELECT * FROM capacity_contracts",
            "row_count": 2,
            "columns": ["account_id", "status"],
            "message": "Consumption is declining because of X.",
        }
        add_turn(conv_id, "Why is this account at risk?", result)
        turn = get_history(conv_id)[0]
        assert turn["type"] == "hybrid"
        assert turn["sql"] == result["generated_sql"]
        assert turn["message"] == result["message"]
    finally:
        clear_conversation(conv_id)
