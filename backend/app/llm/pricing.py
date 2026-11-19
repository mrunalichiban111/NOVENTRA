"""Centralized price registry, keyed by model id. USD per million tokens.

Prices verified from https://platform.claude.com/docs/en/about-claude/pricing on 2026-11-19.
They apply to requests with at most 100K input tokens; larger requests are refused
(see MAX_INPUT_TOKENS) so long-context pricing never applies. Cache write is the
5-minute rate. Costs derived from these numbers are ESTIMATES, not billing.

A model id missing from this registry makes real calls fail closed (PricingMissing).
"""

from dataclasses import dataclass

from app.llm.base import PricingMissing

MAX_INPUT_TOKENS = 100_000


@dataclass(frozen=True)
class Price:
    input: float
    output: float
    cache_read: float
    cache_write: float


PRICES: dict[str, Price] = {
    "claude-sonnet-5-5": Price(input=2.00, output=10.00, cache_read=0.10, cache_write=2.50),
    "claude-haiku-5-5": Price(input=0.10, output=0.50, cache_read=0.01, cache_write=0.125),
}


def get_price(model: str) -> Price:
    try:
        return PRICES[model]
    except KeyError:
        raise PricingMissing(f"no pricing registered for model {model!r}") from None


def estimate_cost_usd(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> float:
    """Estimated cost of a completed call. `input_tokens` excludes cache read/write tokens."""
    p = get_price(model)
    total = (
        input_tokens * p.input
        + output_tokens * p.output
        + cache_read_tokens * p.cache_read
        + cache_write_tokens * p.cache_write
    )
    return total / 1_000_000


def worst_case_cost_usd(model: str, *, est_input_tokens: int, max_tokens: int) -> float:
    """Upper bound for one attempt: all input billed at the input rate, output at max_tokens."""
    p = get_price(model)
    return (est_input_tokens * p.input + max_tokens * p.output) / 1_000_000
