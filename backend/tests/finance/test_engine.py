"""Hand-computed finance cases (FR-FIN-3). Numbers are worked out in the comments."""

import math

import pytest

from app.finance.engine import cash_at, evaluate, six_month_cash
from app.finance.models import PointInputs, Range


def pt(**kw: float) -> PointInputs:
    base = dict(
        unit_price=1000,
        variable_cost_per_unit=400,
        fixed_monthly_cost=30000,
        startup_cost=100000,
        monthly_visitors=5000,
        visitor_to_order_conversion=0.02,
        starting_budget=200000,
    )
    base.update(kw)
    return PointInputs(**base)


def test_profitable_case_every_formula() -> None:
    r = evaluate(pt())
    assert r.contribution_margin == 600  # 1000 - 400
    assert r.contribution_margin_pct == pytest.approx(0.6)  # 600 / 1000
    assert r.monthly_orders == pytest.approx(100)  # 5000 * 0.02
    assert r.monthly_operating_profit == pytest.approx(30000)  # 100*600 - 30000
    assert r.monthly_net_cash_flow == r.monthly_operating_profit
    assert r.opening_cash == 100000  # 200000 - 100000
    assert r.six_month_cash == pytest.approx(280000)  # 100000 + 6*30000
    assert r.break_even_units == 50  # ceil(30000/600)
    assert r.break_even_month == 4  # 4*30000=120000 >= 100000, 3*30000 < 100000
    assert r.runway_status == "self_sustaining"
    assert r.runway_months is None
    assert not r.cash_negative_at_start
    assert r.warnings == ()


def test_cash_by_month_covers_horizon() -> None:
    r = evaluate(pt())
    assert len(r.cash_by_month) == 25  # month 0..24
    assert r.cash_by_month[0] == 100000
    assert r.cash_by_month[6] == pytest.approx(280000)
    assert r.cash_by_month[24] == pytest.approx(100000 + 24 * 30000)


def test_burning_case_runway_and_no_break_even() -> None:
    p = pt(
        unit_price=100,
        variable_cost_per_unit=60,
        fixed_monthly_cost=20000,
        startup_cost=50000,
        monthly_visitors=1000,
        visitor_to_order_conversion=0.05,
        starting_budget=80000,
    )
    r = evaluate(p)
    assert r.contribution_margin == 40
    assert r.monthly_orders == pytest.approx(50)
    assert r.monthly_operating_profit == pytest.approx(-18000)  # 50*40 - 20000
    assert r.six_month_cash == pytest.approx(-78000)  # 30000 - 6*18000
    assert r.break_even_units == 500  # ceil(20000/40)
    assert r.break_even_month is None
    assert r.runway_status == "finite"
    assert r.runway_months == 1  # floor(30000/18000): cash 12000, then -6000


def test_break_even_units_rounds_up() -> None:
    assert evaluate(pt(fixed_monthly_cost=30001)).break_even_units == 51  # 50.0017 -> 51


def test_break_even_units_float_noise_does_not_round_up() -> None:
    # 0.3 / 0.1 == 2.9999999999999996 in floats; exact answer is 3
    assert (
        evaluate(
            pt(unit_price=0.4, variable_cost_per_unit=0.3, fixed_monthly_cost=0.3)
        ).break_even_units
        == 3
    )


def test_zero_fixed_cost_break_even_units_is_zero() -> None:
    assert evaluate(pt(fixed_monthly_cost=0)).break_even_units == 0


def test_zero_price_has_no_percentage_and_is_flagged() -> None:
    r = evaluate(pt(unit_price=0))
    assert r.contribution_margin == -400
    assert r.contribution_margin_pct is None  # division by zero price
    assert r.break_even_units is None
    assert r.break_even_month is None
    assert any("price" in w for w in r.warnings)
    assert all(math.isfinite(c) for c in r.cash_by_month)


def test_zero_conversion_means_no_orders_and_pure_burn() -> None:
    r = evaluate(pt(visitor_to_order_conversion=0))
    assert r.monthly_orders == 0
    assert r.monthly_operating_profit == -30000
    assert r.break_even_month is None
    assert r.runway_status == "finite"
    assert r.runway_months == 3  # floor(100000/30000)
    assert r.break_even_units == 50  # still defined by margin alone


def test_zero_visitors_same_as_zero_conversion() -> None:
    assert evaluate(pt(monthly_visitors=0)).monthly_orders == 0


@pytest.mark.parametrize("cost", [1000, 1200])  # zero and negative margin
def test_non_positive_margin(cost: float) -> None:
    r = evaluate(pt(variable_cost_per_unit=cost))
    assert r.contribution_margin == 1000 - cost
    assert r.break_even_units is None
    assert r.break_even_month is None
    assert any("margin" in w for w in r.warnings)
    assert r.monthly_operating_profit < 0


def test_negative_margin_percentage_is_negative_not_hidden() -> None:
    r = evaluate(pt(variable_cost_per_unit=1200))
    assert r.contribution_margin_pct == pytest.approx(-0.2)


def test_startup_exceeds_budget_is_negative_cash_with_zero_runway() -> None:
    r = evaluate(pt(startup_cost=250000))  # opening cash -50000
    assert r.cash_negative_at_start
    assert r.opening_cash == -50000
    assert r.runway_status == "underfunded"
    assert r.runway_months == 0
    assert r.six_month_cash == pytest.approx(130000)  # -50000 + 180000, still computed
    assert any("startup" in w.lower() for w in r.warnings)


def test_break_even_month_never_within_horizon() -> None:
    # profit 1000/month (60000-59000), startup 100000 -> month 100, beyond the 24-month horizon
    p = pt(fixed_monthly_cost=59000)
    r = evaluate(p)
    assert r.monthly_operating_profit == pytest.approx(1000)
    assert r.break_even_month is None


def test_break_even_month_exact_boundary() -> None:
    # profit 25000/month, startup 100000 -> exactly month 4
    r = evaluate(pt(fixed_monthly_cost=35000))
    assert r.monthly_operating_profit == pytest.approx(25000)
    assert r.break_even_month == 4


def test_zero_startup_break_even_is_first_month_when_not_losing() -> None:
    r = evaluate(pt(startup_cost=0, fixed_monthly_cost=60000))  # profit exactly 0
    assert r.monthly_operating_profit == pytest.approx(0)
    assert r.break_even_month == 1


def test_runway_beyond_horizon() -> None:
    # burn 100/month, opening cash 100000 -> 1000 months
    r = evaluate(pt(fixed_monthly_cost=60100, startup_cost=0, starting_budget=100000))
    assert r.monthly_operating_profit == pytest.approx(-100)
    assert r.runway_status == "beyond_horizon"
    assert r.runway_months is None


def test_six_month_cash_helper_matches_evaluate_and_cash_at() -> None:
    p = pt()
    assert six_month_cash(p) == pytest.approx(evaluate(p).six_month_cash)
    assert cash_at(p, 0) == 100000
    assert cash_at(p, 3) == pytest.approx(190000)


def test_six_month_metric_independent_of_24_month_projection() -> None:
    # Loses money in 6 months but would recover later: month-6 cash and break-even are separate.
    p = pt(fixed_monthly_cost=59000)  # profit 1000
    r = evaluate(p)
    assert r.six_month_cash == pytest.approx(106000)  # still > 0
    assert r.break_even_month is None


@pytest.mark.parametrize(
    "bad",
    [
        {"unit_price": -1},
        {"variable_cost_per_unit": float("nan")},
        {"fixed_monthly_cost": float("inf")},
        {"startup_cost": -0.01},
        {"monthly_visitors": -5},
        {"visitor_to_order_conversion": 1.5},
        {"starting_budget": -1},
        {"unit_price": True},
        {"unit_price": "100"},
    ],
)
def test_invalid_point_inputs_raise(bad: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        pt(**bad)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "args", [(5, 1, 9), (1, 9, 5), (-1, 0, 1), (0, 1, float("nan")), (0, 0, float("inf"))]
)
def test_invalid_range_raises(args: tuple[float, float, float]) -> None:
    with pytest.raises(ValueError):
        Range(*args)


def test_equal_range_is_valid() -> None:
    assert Range(3, 3, 3).base == 3


# --- runway semantics (see config.py for the exact meaning of each status) ---------------


def test_runway_zero_opening_cash_with_loss_is_finite_zero() -> None:
    r = evaluate(pt(starting_budget=100000, fixed_monthly_cost=61000))  # profit -1000
    assert r.opening_cash == 0
    assert (r.runway_status, r.runway_months) == ("finite", 0)
    assert not r.cash_negative_at_start


def test_runway_zero_opening_cash_with_profit_is_self_sustaining() -> None:
    r = evaluate(pt(starting_budget=100000))  # opening 0, profit +30000
    assert r.opening_cash == 0
    assert (r.runway_status, r.runway_months) == ("self_sustaining", None)


def test_runway_zero_profit_is_self_sustaining_even_with_cash() -> None:
    r = evaluate(pt(fixed_monthly_cost=60000))  # profit exactly 0, opening 100000
    assert (r.runway_status, r.runway_months) == ("self_sustaining", None)


def test_runway_positive_profit_is_self_sustaining() -> None:
    assert evaluate(pt()).runway_status == "self_sustaining"


def test_runway_underfunded_even_when_profitable() -> None:
    r = evaluate(pt(startup_cost=250000))  # opening -50000, profit +30000
    assert (r.runway_status, r.runway_months) == ("underfunded", 0)
    assert r.cash_negative_at_start


def test_runway_fractional_rounds_down() -> None:
    # opening 100000, burn 40000 -> 2.5 months: cash 20000 after month 2, -20000 after month 3
    r = evaluate(pt(fixed_monthly_cost=100000))
    assert r.monthly_operating_profit == pytest.approx(-40000)
    assert (r.runway_status, r.runway_months) == ("finite", 2)
    assert r.cash_by_month[2] > 0 > r.cash_by_month[3]


def test_runway_exact_integer_ratio_counts_the_zero_cash_month() -> None:
    # opening 100000, burn 50000 -> exactly 2: cash is exactly 0 after month 2 (still >= 0)
    r = evaluate(pt(fixed_monthly_cost=110000))
    assert (r.runway_status, r.runway_months) == ("finite", 2)
    assert r.cash_by_month[2] == pytest.approx(0)


def test_runway_horizon_boundary() -> None:
    # burn 1000, opening 24000 -> 24 months (inside horizon); opening 25000 -> 25 (beyond)
    inside = evaluate(pt(fixed_monthly_cost=61000, startup_cost=0, starting_budget=24000))
    assert (inside.runway_status, inside.runway_months) == ("finite", 24)
    beyond = evaluate(pt(fixed_monthly_cost=61000, startup_cost=0, starting_budget=25000))
    assert (beyond.runway_status, beyond.runway_months) == ("beyond_horizon", None)
