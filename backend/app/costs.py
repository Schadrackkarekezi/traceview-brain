"""
Rough cost estimation for OpenAI API calls, for the observability dashboard.

Rates are approximate and will drift as OpenAI changes pricing - treat this
as "good enough to spot a cost spike," not a billing-accurate ledger. Check
current rates at openai.com/api/pricing before trusting this for real
budget decisions.
"""

# $ per 1M tokens: (input, output)
PRICING_PER_MILLION_TOKENS = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
    "gpt-4.1": (2.00, 8.00),
    "gpt-4.1-mini": (0.40, 1.60),
}

DEFAULT_RATE = (2.50, 10.00)  # falls back to gpt-4o-ish pricing for unlisted models


def estimate_cost_usd(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    input_rate, output_rate = PRICING_PER_MILLION_TOKENS.get(model, DEFAULT_RATE)
    cost = (prompt_tokens / 1_000_000) * input_rate + (completion_tokens / 1_000_000) * output_rate
    return round(cost, 6)
