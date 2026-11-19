"""Scores E, R and overall (SRS Section 6), hand computed."""

import pytest

from app.finance import config
from app.finance.scoring import (
    RiskSeverity,
    display_score,
    economics_score,
    overall_prelaunch,
    risk_score,
)


def sev(**kw: int) -> dict[str, RiskSeverity]:
    base = dict.fromkeys(config.RISK_WEIGHTS, 3)
    base.update(kw)
    return {k: RiskSeverity(v, f"because {k}") for k, v in base.items()}


def test_weights_sum_to_one() -> None:
    assert sum(config.RISK_WEIGHTS.values()) == pytest.approx(1.0)
    assert sum(config.PRELAUNCH_WEIGHTS.values()) == pytest.approx(1.0)


def test_economics_score() -> None:
    assert economics_score(0.0) == 0
    assert economics_score(0.5) == 50
    assert economics_score(0.8765) == pytest.approx(87.65, abs=1e-9)  # full precision
    assert economics_score(1.0) == 100


@pytest.mark.parametrize("p", [-0.01, 1.01, float("nan")])
def test_economics_score_rejects_bad_probability(p: float) -> None:
    with pytest.raises(ValueError):
        economics_score(p)


def test_risk_score_all_ones_is_100() -> None:
    assert risk_score(sev(**dict.fromkeys(config.RISK_WEIGHTS, 1))) == pytest.approx(100)


def test_risk_score_all_fives_is_0() -> None:
    assert risk_score(sev(**dict.fromkeys(config.RISK_WEIGHTS, 5))) == pytest.approx(0, abs=1e-9)


def test_risk_score_all_threes_is_50() -> None:
    assert risk_score(sev()) == pytest.approx(50)  # mean 3 -> 100*(1-(2/4))


def test_risk_score_weighted_hand_computed() -> None:
    # sat 5, ops 4, compliance 2, capital 3, founder 1
    # mean = .30*5 + .25*4 + .20*2 + .15*3 + .10*1 = 1.5+1.0+0.4+0.45+0.1 = 3.45
    # R = 100*(1 - 2.45/4) = 38.75
    s = sev(
        market_saturation=5, operational_complexity=4, compliance=2, capital_need=3, founder_load=1
    )
    assert risk_score(s) == pytest.approx(38.75)


def test_risk_weights_are_applied_per_category() -> None:
    # only market saturation = 5, rest 1: mean = 1 + .30*4 = 2.2 -> R = 70
    ones = dict.fromkeys(config.RISK_WEIGHTS, 1)
    assert risk_score(sev(**{**ones, "market_saturation": 5})) == pytest.approx(70)
    # only founder load = 5: mean = 1 + .10*4 = 1.4 -> R = 90
    assert risk_score(sev(**{**ones, "founder_load": 5})) == pytest.approx(90)


def test_risk_requires_exactly_the_five_categories() -> None:
    s = sev()
    del s["compliance"]
    with pytest.raises(ValueError):
        risk_score(s)
    s2 = sev()
    s2["extra"] = RiskSeverity(3, "x")
    with pytest.raises(ValueError):
        risk_score(s2)


@pytest.mark.parametrize("bad", [0, 6, -1, 2.5, True, "3"])
def test_severity_must_be_integer_1_to_5(bad: object) -> None:
    with pytest.raises(ValueError):
        RiskSeverity(bad, "why")  # type: ignore[arg-type]


@pytest.mark.parametrize("why", ["", "   "])
def test_severity_requires_justification(why: str) -> None:
    with pytest.raises(ValueError):
        RiskSeverity(3, why)


def test_overall_prelaunch() -> None:
    assert overall_prelaunch(100, 100) == pytest.approx(100)
    assert overall_prelaunch(0, 0) == 0
    assert overall_prelaunch(80, 50) == pytest.approx(67.4)  # 0.58*80 + 0.42*50 = 46.4 + 21 = 67.4
    assert overall_prelaunch(50, 100) == pytest.approx(71)  # 29 + 42


@pytest.mark.parametrize("e,r", [(-1, 50), (50, 101), (float("nan"), 5)])
def test_overall_rejects_out_of_range(e: float, r: float) -> None:
    with pytest.raises(ValueError):
        overall_prelaunch(e, r)


def test_scores_are_full_precision_and_only_display_rounds() -> None:
    e = economics_score(0.123456789)
    assert e == pytest.approx(12.3456789, abs=1e-12)  # not rounded
    assert e != display_score(e)
    assert display_score(e) == 12.35
    assert display_score(64.996) == 65.0


def test_float_noise_never_leaves_the_0_100_range() -> None:
    assert 0 <= risk_score(sev(**dict.fromkeys(config.RISK_WEIGHTS, 5))) <= 100
    assert 0 <= risk_score(sev(**dict.fromkeys(config.RISK_WEIGHTS, 1))) <= 100
    assert 0 <= overall_prelaunch(100, 100) <= 100
