"""Deterministic finance engine (FR-FIN-3). All arithmetic is Python; conventions: see config.py."""

import math
from dataclasses import dataclass
from typing import Literal

from app.finance import config
from app.finance.models import PointInputs

RunwayStatus = Literal["self_sustaining", "finite", "beyond_horizon", "underfunded"]


@dataclass(frozen=True)
class FinanceResult:
    contribution_margin: float
    contribution_margin_pct: float | None  # None when unit price is 0 (division by zero)
    monthly_orders: float
    monthly_operating_profit: float
    monthly_net_cash_flow: float
    opening_cash: float  # starting budget - startup cost (month 0); may be negative
    six_month_cash: float  # cumulative cash at config.PROFIT_MONTH
    cash_by_month: tuple[float, ...]  # months 0..PROJECTION_HORIZON_MONTHS
    break_even_units: int | None  # None unless contribution margin > 0
    break_even_month: int | None  # None if not recovered within the horizon
    runway_months: int | None  # None unless runway_status == "finite"
    runway_status: RunwayStatus
    cash_negative_at_start: bool
    warnings: tuple[str, ...]


def _margin(p: PointInputs) -> float:
    return p.unit_price - p.variable_cost_per_unit


def _monthly_profit(p: PointInputs) -> float:
    orders = p.monthly_visitors * p.visitor_to_order_conversion
    return orders * _margin(p) - p.fixed_monthly_cost


def cash_at(p: PointInputs, month: int) -> float:
    """Cumulative cash at `month` (0 = right after paying the startup cost)."""
    if month < 0:
        raise ValueError("month must be >= 0")
    return p.starting_budget - p.startup_cost + month * _monthly_profit(p)


def six_month_cash(p: PointInputs) -> float:
    """Cumulative cash at month 6; the quantity the Monte Carlo and score E are based on."""
    return cash_at(p, config.PROFIT_MONTH)


def _break_even_units(margin: float, fixed: float) -> int | None:
    if margin <= 0:
        return None
    # round first so float noise (0.3/0.1 = 2.9999999999999996) cannot move the ceiling
    return math.ceil(round(fixed / margin, 9))


def _break_even_month(profit: float, startup: float) -> int | None:
    for month in range(1, config.PROJECTION_HORIZON_MONTHS + 1):
        if month * profit >= startup - config.CASH_EPSILON:
            return month
    return None


def _runway(opening: float, profit: float) -> tuple[int | None, RunwayStatus]:
    if opening < 0:
        return 0, "underfunded"
    if profit >= 0:
        return None, "self_sustaining"
    months = math.floor(round(opening / -profit, 9))
    if months > config.PROJECTION_HORIZON_MONTHS:
        return None, "beyond_horizon"
    return months, "finite"


def evaluate(p: PointInputs) -> FinanceResult:
    margin = _margin(p)
    orders = p.monthly_visitors * p.visitor_to_order_conversion
    profit = orders * margin - p.fixed_monthly_cost
    opening = p.starting_budget - p.startup_cost
    runway_months, runway_status = _runway(opening, profit)

    warnings: list[str] = []
    if p.unit_price == 0:
        warnings.append("Unit price is 0: contribution margin percentage is undefined.")
    if margin <= 0:
        warnings.append("Contribution margin is not positive: every order loses money.")
    if opening < 0:
        warnings.append("Startup cost exceeds the starting budget: cash is negative at start.")
    if orders == 0:
        warnings.append("No orders per month: visitors or conversion is 0.")

    return FinanceResult(
        contribution_margin=margin,
        contribution_margin_pct=None if p.unit_price == 0 else margin / p.unit_price,
        monthly_orders=orders,
        monthly_operating_profit=profit,
        monthly_net_cash_flow=profit,
        opening_cash=opening,
        six_month_cash=opening + config.PROFIT_MONTH * profit,
        cash_by_month=tuple(
            opening + m * profit for m in range(config.PROJECTION_HORIZON_MONTHS + 1)
        ),
        break_even_units=_break_even_units(margin, p.fixed_monthly_cost),
        break_even_month=_break_even_month(profit, p.startup_cost),
        runway_months=runway_months,
        runway_status=runway_status,
        cash_negative_at_start=opening < 0,
        warnings=tuple(warnings),
    )
