"""Validated finance inputs. Invalid numbers raise ValueError; nothing is silently clamped."""

import math
from dataclasses import dataclass

from app.finance import config


def _check_number(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{name} must be a number, got {value!r}")
    if not math.isfinite(value):
        raise ValueError(f"{name} must be finite, got {value!r}")
    if value < 0:
        raise ValueError(f"{name} must be >= 0, got {value!r}")
    return float(value)


@dataclass(frozen=True)
class Range:
    """Low/base/high for one assumption (FR-FIN-1). Requires low <= base <= high."""

    low: float
    base: float
    high: float

    def __post_init__(self) -> None:
        for field in ("low", "base", "high"):
            object.__setattr__(self, field, _check_number(field, getattr(self, field)))
        if not self.low <= self.base <= self.high:
            raise ValueError(f"need low <= base <= high, got {self.low}, {self.base}, {self.high}")


@dataclass(frozen=True)
class PointInputs:
    """One concrete value per assumption: a scenario or a single Monte Carlo draw."""

    unit_price: float
    variable_cost_per_unit: float
    fixed_monthly_cost: float
    startup_cost: float
    monthly_visitors: float
    visitor_to_order_conversion: float
    starting_budget: float

    def __post_init__(self) -> None:
        for field in (*config.ASSUMPTION_KEYS, "starting_budget"):
            object.__setattr__(self, field, _check_number(field, getattr(self, field)))
        if self.visitor_to_order_conversion > 1:
            raise ValueError("visitor_to_order_conversion is a fraction and must be <= 1")


@dataclass(frozen=True)
class FinanceInputs:
    """The six low/base/high assumptions plus the founder's starting budget."""

    unit_price: Range
    variable_cost_per_unit: Range
    fixed_monthly_cost: Range
    startup_cost: Range
    monthly_visitors: Range
    visitor_to_order_conversion: Range
    starting_budget: float

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "starting_budget", _check_number("starting_budget", self.starting_budget)
        )
        if self.visitor_to_order_conversion.high > 1:
            raise ValueError("visitor_to_order_conversion is a fraction and must be <= 1")

    def ranges(self) -> dict[str, Range]:
        return {key: getattr(self, key) for key in config.ASSUMPTION_KEYS}
