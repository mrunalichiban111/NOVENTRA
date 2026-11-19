"""Scenario endpoint selection (FR-FIN-4), hand computed."""

import pytest

from app.finance.models import FinanceInputs, Range
from app.finance.scenarios import run_scenarios, scenario_point


def inputs() -> FinanceInputs:
    return FinanceInputs(
        unit_price=Range(800, 1000, 1200),
        variable_cost_per_unit=Range(300, 400, 500),
        fixed_monthly_cost=Range(25000, 30000, 40000),
        startup_cost=Range(80000, 100000, 150000),
        monthly_visitors=Range(4000, 5000, 6000),
        visitor_to_order_conversion=Range(0.01, 0.02, 0.03),
        starting_budget=200000,
    )


def test_pessimistic_uses_adverse_endpoint_of_every_input() -> None:
    p = scenario_point(inputs(), "pessimistic")
    assert p.unit_price == 800  # higher is better -> low
    assert p.variable_cost_per_unit == 500  # cost -> high
    assert p.fixed_monthly_cost == 40000
    assert p.startup_cost == 150000
    assert p.monthly_visitors == 4000
    assert p.visitor_to_order_conversion == 0.01
    assert p.starting_budget == 200000


def test_optimistic_uses_favorable_endpoint_of_every_input() -> None:
    p = scenario_point(inputs(), "optimistic")
    assert (p.unit_price, p.variable_cost_per_unit) == (1200, 300)
    assert (p.fixed_monthly_cost, p.startup_cost) == (25000, 80000)
    assert (p.monthly_visitors, p.visitor_to_order_conversion) == (6000, 0.03)


def test_base_uses_base_values() -> None:
    p = scenario_point(inputs(), "base")
    assert (p.unit_price, p.startup_cost, p.visitor_to_order_conversion) == (1000, 100000, 0.02)


def test_scenario_results() -> None:
    res = run_scenarios(inputs())
    assert list(res) == ["pessimistic", "base", "optimistic"]
    # pessimistic: cm 300, orders 40, profit 12000-40000=-28000, opening 50000, cash6 50000-168000
    assert res["pessimistic"].monthly_operating_profit == pytest.approx(-28000)
    assert res["pessimistic"].six_month_cash == pytest.approx(-118000)
    assert res["base"].six_month_cash == pytest.approx(280000)
    # optimistic: cm 900, orders 180, profit 162000-25000=137000, opening 120000
    assert res["optimistic"].monthly_operating_profit == pytest.approx(137000)
    assert res["optimistic"].six_month_cash == pytest.approx(942000)


def test_scenarios_are_ordered_pessimistic_le_base_le_optimistic() -> None:
    res = run_scenarios(inputs())
    cash = [res[k].six_month_cash for k in ("pessimistic", "base", "optimistic")]
    assert cash == sorted(cash)


def test_unknown_scenario_raises() -> None:
    with pytest.raises(ValueError):
        scenario_point(inputs(), "wild")


def test_degenerate_ranges_give_identical_scenarios() -> None:
    r = Range(5, 5, 5)
    same = FinanceInputs(r, Range(1, 1, 1), r, r, r, Range(0.1, 0.1, 0.1), 1000)
    res = run_scenarios(same)
    assert res["pessimistic"] == res["base"] == res["optimistic"]


def test_conversion_range_above_one_rejected() -> None:
    with pytest.raises(ValueError):
        FinanceInputs(
            Range(1, 1, 1),
            Range(0, 0, 0),
            Range(0, 0, 0),
            Range(0, 0, 0),
            Range(1, 1, 1),
            Range(0.5, 1.0, 1.2),
            0,
        )
