"""Seeded Monte Carlo over the assumption ranges (FR-FIN-5).

Each simulation draws every assumption independently from Triangular(low, base, high) in
the fixed order of config.ASSUMPTION_KEYS (so startup cost is drawn exactly once per
simulation), then computes month-6 cumulative cash with the same formula as the engine.
Percentiles use linear interpolation between order statistics (numpy's default method).
The seed is an input: tests pass fixed seeds; production calls new_seed() once per analysis
run and persists it so results reproduce.
"""

import math
import random
import secrets
from dataclasses import dataclass

from app.finance import config
from app.finance.engine import six_month_cash
from app.finance.models import FinanceInputs, PointInputs


@dataclass(frozen=True)
class MonteCarloResult:
    seed: int
    n: int
    p_profitable: float  # P(month-6 cumulative cash > 0)
    p10: float
    p50: float
    p90: float


def new_seed() -> int:
    return secrets.randbits(63)


def percentile(values: list[float], pct: float) -> float:
    if not values:
        raise ValueError("percentile of empty data")
    data = sorted(values)
    pos = (len(data) - 1) * pct / 100
    lo = math.floor(pos)
    hi = math.ceil(pos)
    return data[lo] + (data[hi] - data[lo]) * (pos - lo)


def simulate(inputs: FinanceInputs, seed: int, n: int = config.MC_SIMULATIONS) -> MonteCarloResult:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError(f"seed must be an int, got {seed!r}")
    if isinstance(n, bool) or not isinstance(n, int) or n < 1:
        raise ValueError(f"n must be a positive int, got {n!r}")
    rng = random.Random(seed)
    ranges = inputs.ranges()
    cash: list[float] = []
    for _ in range(n):
        draw = {k: rng.triangular(r.low, r.high, r.base) for k, r in ranges.items()}
        cash.append(six_month_cash(PointInputs(starting_budget=inputs.starting_budget, **draw)))
    p10, p50, p90 = (percentile(cash, q) for q in config.MC_PERCENTILES)
    profitable = sum(1 for c in cash if c > 0)
    return MonteCarloResult(seed, n, profitable / n, p10, p50, p90)
