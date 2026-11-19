"""Verdict boundaries and precedence (SRS Section 6, FR-SC-3)."""

import pytest

from app.finance.verdict import Verdict, decide_verdict


def v(overall: float, e: float = 60, r: float = 60) -> Verdict:
    return decide_verdict(overall, e=e, r=r).verdict


@pytest.mark.parametrize(
    "overall,expected",
    [
        (44.9, Verdict.RETHINK),
        (45.0, Verdict.GO_WITH_CHANGES),
        (64.9, Verdict.GO_WITH_CHANGES),
        (65.0, Verdict.GO),
        (100, Verdict.GO),
        (0, Verdict.RETHINK),
    ],
)
def test_overall_boundaries(overall: float, expected: Verdict) -> None:
    assert v(overall) is expected


@pytest.mark.parametrize(
    "e,expected",
    [(9.9, Verdict.RETHINK), (10.0, Verdict.GO_WITH_CHANGES), (0.0, Verdict.RETHINK)],
)
def test_e_below_10_is_rethink_even_when_overall_is_high(e: float, expected: Verdict) -> None:
    # overall 70 would otherwise be Go-eligible; E=10 still has a component < 40
    assert decide_verdict(70, e=e, r=100).verdict is expected


def test_e_just_under_10_rethink_precedes_everything() -> None:
    assert decide_verdict(80, e=9.9, r=100).verdict is Verdict.RETHINK


@pytest.mark.parametrize(
    "component,expected",
    [(39.9, Verdict.GO_WITH_CHANGES), (40.0, Verdict.GO)],
)
def test_component_floor_blocks_go(component: float, expected: Verdict) -> None:
    assert decide_verdict(70, e=component, r=90).verdict is expected
    assert decide_verdict(70, e=90, r=component).verdict is expected


def test_overall_exactly_65_with_weak_component_is_go_with_changes() -> None:
    assert decide_verdict(65, e=39.9, r=90).verdict is Verdict.GO_WITH_CHANGES


def test_overall_exactly_45_with_weak_component_is_go_with_changes() -> None:
    assert decide_verdict(45, e=30, r=60).verdict is Verdict.GO_WITH_CHANGES


def test_verdict_path_has_no_demand_argument() -> None:
    # M3 is strictly pre-launch: D cannot influence the verdict.
    with pytest.raises(TypeError):
        decide_verdict(70, e=80, r=80, d=10)  # type: ignore[call-arg]


def test_display_rounding_never_changes_the_verdict() -> None:
    from app.finance.scoring import display_score, overall_prelaunch

    assert display_score(64.996) == 65.0  # what the user would see
    assert v(64.996) is Verdict.GO_WITH_CHANGES  # ...but the full-precision score decides
    assert v(44.999) is Verdict.RETHINK
    assert decide_verdict(70, e=39.9999, r=90).verdict is Verdict.GO_WITH_CHANGES
    assert decide_verdict(70, e=9.9999, r=90).verdict is Verdict.RETHINK
    # end to end: E=100, R chosen so overall is just under 65
    r = 100.0
    e = (64.996 - 0.42 * r) / 0.58  # overall = 0.58 e + 0.42 r = 64.996
    assert overall_prelaunch(e, r) == pytest.approx(64.996)
    assert decide_verdict(overall_prelaunch(e, r), e=e, r=r).verdict is Verdict.GO_WITH_CHANGES


def test_explanation_does_not_claim_the_threshold_was_met_when_just_below() -> None:
    result = decide_verdict(64.996, e=60, r=60)
    assert any("64.996" in line for line in result.explanation)


def test_explanations_name_the_driving_scores() -> None:
    go = decide_verdict(70, e=80, r=60)
    assert any("70" in line for line in go.explanation)
    assert go.explanation  # never empty

    low_e = decide_verdict(60, e=5, r=100)
    assert low_e.verdict is Verdict.RETHINK
    assert any("Economics" in line and "5" in line for line in low_e.explanation)

    weak = decide_verdict(70, e=35, r=90)
    assert weak.verdict is Verdict.GO_WITH_CHANGES
    assert any("Economics" in line and "40" in line for line in weak.explanation)

    low_overall = decide_verdict(44.9, e=50, r=40)
    assert any("44.9" in line and "45" in line for line in low_overall.explanation)


def test_verdict_values_match_scorecard_db_check() -> None:
    assert {m.value for m in Verdict} == {"go", "go_with_changes", "rethink"}


@pytest.mark.parametrize("bad", [-0.1, 100.1, float("nan")])
def test_invalid_scores_raise(bad: float) -> None:
    with pytest.raises(ValueError):
        decide_verdict(bad, e=50, r=50)
    with pytest.raises(ValueError):
        decide_verdict(50, e=bad, r=50)
