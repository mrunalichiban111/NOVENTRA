"""Demand score D and its standard error (SRS Section 6). Pure."""

import math

import pytest

from app.finance.scoring import ComparableKey, comparable, demand_score


def test_d_equals_converted_over_total_and_se() -> None:
    rates = [1.0] * 10 + [0.0] * 20
    d, se = demand_score(rates)
    assert d == pytest.approx(100 / 3)
    sd = math.sqrt((10 * (2 / 3) ** 2 + 20 * (1 / 3) ** 2) / 29)
    assert se == pytest.approx(100 * sd / math.sqrt(30))


def test_partial_rates_and_bounds() -> None:
    d, se = demand_score([1 / 3, 2 / 3, 1.0])
    assert d == pytest.approx(200 / 3)
    assert demand_score([0.5])[1] == 0.0
    with pytest.raises(ValueError):
        demand_score([])
    with pytest.raises(ValueError):
        demand_score([1.2])


def test_comparability_requires_complete_and_identical_keys() -> None:
    k = ComparableKey("panel", "claude-haiku-5-5", "abc", "d-v1")
    assert comparable(k, True, k, True)
    assert not comparable(k, True, k, False)
    assert not comparable(k, True, ComparableKey("panel", "claude-haiku-5-5", "xyz", "d-v1"), True)
    assert not comparable(k, True, ComparableKey("other", "claude-haiku-5-5", "abc", "d-v1"), True)
    empty = ComparableKey("panel", None, None, None)
    assert not comparable(empty, True, empty, True)
