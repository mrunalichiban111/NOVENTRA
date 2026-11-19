"""Seeded Monte Carlo (FR-FIN-5)."""

import random

import pytest

from app.finance import config
from app.finance.models import FinanceInputs, Range
from app.finance.montecarlo import new_seed, percentile, simulate


def fixed(**kw: Range) -> FinanceInputs:
    d = dict(
        unit_price=Range(1000, 1000, 1000),
        variable_cost_per_unit=Range(400, 400, 400),
        fixed_monthly_cost=Range(30000, 30000, 30000),
        startup_cost=Range(100000, 100000, 100000),
        monthly_visitors=Range(5000, 5000, 5000),
        visitor_to_order_conversion=Range(0.02, 0.02, 0.02),
    )
    d.update(kw)
    return FinanceInputs(starting_budget=200000, **d)  # type: ignore[arg-type]


def wide() -> FinanceInputs:
    return fixed(
        unit_price=Range(800, 1000, 1200),
        variable_cost_per_unit=Range(300, 400, 500),
        fixed_monthly_cost=Range(25000, 30000, 40000),
        startup_cost=Range(80000, 100000, 150000),
        monthly_visitors=Range(4000, 5000, 6000),
        visitor_to_order_conversion=Range(0.01, 0.02, 0.03),
    )


def test_default_simulation_count_is_2000() -> None:
    assert config.MC_SIMULATIONS == 2000
    assert simulate(wide(), seed=1).n == 2000


def test_same_seed_same_result() -> None:
    assert simulate(wide(), seed=42) == simulate(wide(), seed=42)


def test_different_seed_different_result() -> None:
    a, b = simulate(wide(), seed=1), simulate(wide(), seed=2)
    assert (a.p10, a.p50, a.p90) != (b.p10, b.p50, b.p90)


def test_result_records_seed() -> None:
    assert simulate(wide(), seed=7).seed == 7


def test_degenerate_ranges_collapse_to_base_case() -> None:
    r = simulate(fixed(), seed=3, n=50)
    assert r.p_profitable == 1.0
    assert r.p10 == r.p50 == r.p90 == pytest.approx(280000)


def test_certain_loss_gives_zero_probability() -> None:
    r = simulate(fixed(fixed_monthly_cost=Range(90000, 90000, 90000)), seed=3, n=50)
    assert r.p_profitable == 0.0
    assert r.p90 < 0


def test_zero_cash_is_not_profitable() -> None:
    # cash6 = 200000 - 100000 + 6*profit; profit -> -(100000/6) is awkward, so use budget=startup, profit 0
    inp = FinanceInputs(
        Range(1000, 1000, 1000),
        Range(400, 400, 400),
        Range(60000, 60000, 60000),
        Range(100000, 100000, 100000),
        Range(5000, 5000, 5000),
        Range(0.02, 0.02, 0.02),
        100000,
    )
    r = simulate(inp, seed=1, n=10)
    assert r.p50 == pytest.approx(0)
    assert r.p_profitable == 0.0  # strictly greater than zero required


def test_analytic_single_variable_probability() -> None:
    # cash6 = 380000 - startup, startup ~ Triangular(100000, mode 200000, high 500000).
    # P(startup < 380000) = 1 - (500000-380000)^2 / ((500000-100000)*(500000-200000)) = 0.88
    inp = fixed(startup_cost=Range(100000, 200000, 500000))
    r = simulate(inp, seed=42)
    assert r.p_profitable == pytest.approx(0.88, abs=0.04)


def test_percentiles_are_ordered() -> None:
    r = simulate(wide(), seed=5)
    assert r.p10 <= r.p50 <= r.p90
    assert 0.0 <= r.p_profitable <= 1.0


def test_six_independent_draws_per_simulation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = 0
    orig = random.Random.triangular

    def counting(self: random.Random, *a: float, **k: float) -> float:
        nonlocal calls
        calls += 1
        return orig(self, *a, **k)

    monkeypatch.setattr(random.Random, "triangular", counting)
    simulate(wide(), seed=1, n=100)
    assert calls == 600  # 6 assumptions, startup cost drawn exactly once per simulation


def test_draws_stay_within_ranges_so_cash_within_scenario_bounds() -> None:
    from app.finance.scenarios import run_scenarios

    res = run_scenarios(wide())
    r = simulate(wide(), seed=9)
    assert res["pessimistic"].six_month_cash <= r.p10
    assert r.p90 <= res["optimistic"].six_month_cash


@pytest.mark.parametrize("n", [0, -1])
def test_invalid_n_raises(n: int) -> None:
    with pytest.raises(ValueError):
        simulate(wide(), seed=1, n=n)


@pytest.mark.parametrize("seed", [1.5, "x", True, None])
def test_invalid_seed_raises(seed: object) -> None:
    with pytest.raises(ValueError):
        simulate(wide(), seed=seed)  # type: ignore[arg-type]


def test_percentile_linear_interpolation() -> None:
    assert percentile([1, 2, 3, 4, 5], 50) == 3
    assert percentile([0, 10], 10) == pytest.approx(1.0)
    assert percentile([0, 10], 90) == pytest.approx(9.0)
    assert percentile([7], 10) == 7
    assert percentile([5, 1, 3], 0) == 1  # sorts first
    assert percentile([5, 1, 3], 100) == 5


def test_percentile_empty_raises() -> None:
    with pytest.raises(ValueError):
        percentile([], 50)


def test_new_seed_is_valid_int() -> None:
    s = new_seed()
    assert isinstance(s, int) and 0 <= s < 2**63
    assert simulate(wide(), seed=s, n=10).seed == s
