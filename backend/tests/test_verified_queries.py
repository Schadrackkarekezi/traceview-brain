"""
Verified-query matching is a real correctness risk, not just a cache: a
false-positive match silently serves the wrong SQL for a question that
only sounds similar. These tests pin the threshold behavior down so a
future change to MATCH_THRESHOLD or the embedding model can't quietly
break it.
"""

import app.verified_queries as verified_queries


def test_exact_match_hits():
    result = verified_queries.find_verified_match("How many accounts do we have?")
    assert result is not None
    assert "accounts" in result["sql"].lower()


def test_paraphrase_hits():
    # Measured at 0.9648 against "How many accounts do we have?" - a real
    # paraphrase, comfortably above MATCH_THRESHOLD (0.92).
    result = verified_queries.find_verified_match("How many accounts do we currently have?")
    assert result is not None


def test_unrelated_question_misses():
    result = verified_queries.find_verified_match("What's the weather like today?")
    assert result is None


def test_adjacent_but_different_question_does_not_false_positive():
    # Same structure as the verified "retail industry" deal-count entry,
    # but scoped to a different industry - measured at 0.7732, well below
    # the 0.92 threshold. Must NOT match just because it's topically close.
    result = verified_queries.find_verified_match(
        "How many deals were logged for accounts in the healthcare industry?"
    )
    assert result is None
