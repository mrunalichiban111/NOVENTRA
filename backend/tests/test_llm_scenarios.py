"""Controlled fixture scenarios: never user text as a path; real calls ignore overrides."""

import json
from decimal import Decimal
from pathlib import Path

import pytest

from app import config
from app.llm import budget as budget_mod
from app.llm.base import ConfigError, LLMRequest
from app.llm.mock import MockProvider
from app.llm.scenarios import check_scenario_id, known_scenarios, scenario_for_idea
from app.llm.schemas import Assumptions, PreMortem
from app.llm.structured import run_structured
from app.models import Assumption
from app.pipeline import analysis
from tests.m3_helpers import idea

FIXTURES = Path(config.FIXTURES_DIR) / "llm"


def req(task="premortem", scenario=None) -> LLMRequest:
    return LLMRequest(
        task=task, model="mock", system="s", user="u", max_tokens=10, timeout_s=1,
        fixture_scenario=scenario,
    )  # fmt: skip


def test_known_scenarios() -> None:
    assert {"good", "rethink", "homemade_food"} <= known_scenarios()


@pytest.mark.parametrize(
    "bad",
    [
        "../etc",
        "good/../good",
        "Good",
        "",
        "a b",
        "good\n",
        "/etc/passwd",
        "x" * 41,
        "nonexistent",
        "..",
        ".",
    ],
)
def test_scenario_ids_are_a_closed_set(bad: str) -> None:
    with pytest.raises(ConfigError):
        check_scenario_id(bad)
    with pytest.raises(ConfigError):
        MockProvider().complete(req(scenario=bad))


def test_idea_text_never_becomes_a_path() -> None:
    # Arbitrary text, including path-like text, maps to no scenario (default fixtures).
    assert scenario_for_idea("../../etc/passwd") is None
    assert scenario_for_idea("anything else at all") is None


def test_known_sample_ideas_map_to_scenarios_regardless_of_whitespace_and_case() -> None:
    assert scenario_for_idea(idea("good")) == "good"
    assert scenario_for_idea("  " + idea("rethink").upper().replace(" ", "  ") + "\n") == "rethink"
    assert scenario_for_idea(idea("homemade_food")) == "homemade_food"


def test_mock_provider_reads_the_scenario_fixture() -> None:
    default = MockProvider().complete(req())
    good = MockProvider().complete(req(scenario="good"))
    assert good.text != default.text
    assert json.loads(good.text)["target_customer"].startswith("Freelancers")


def test_mock_mode_run_structured_honours_the_scenario() -> None:
    out = run_structured("premortem", PreMortem, "x", fixture_scenario="rethink")
    assert "Every online shopper" in out.data.target_customer


def test_unknown_scenario_in_mock_mode_is_an_error() -> None:
    with pytest.raises(ConfigError):
        run_structured("premortem", PreMortem, "x", fixture_scenario="nope")


def test_real_calls_ignore_fixture_overrides(monkeypatch) -> None:
    seen: list[LLMRequest] = []

    class SpyProvider:
        name = "spy"

        def complete(self, request):
            seen.append(request)
            return MockProvider().complete(request)

    class NoGuard:
        def __init__(self, **_):
            pass

        def before(self, *_a):
            return object()

        def after(self, *_a):
            pass

    monkeypatch.setattr(config, "MOCK_LLM", False)
    monkeypatch.setattr(config, "CLAUDE_MODEL_STRONG", "strong-model")
    monkeypatch.setattr(budget_mod, "LLMGuard", NoGuard)
    run_structured(
        "premortem", PreMortem, "x", run_id="r1", provider=SpyProvider(), fixture_scenario="rethink"
    )
    assert seen and all(r.fixture_scenario is None for r in seen)


def test_claude_provider_never_reads_the_override() -> None:
    import inspect

    from app.llm import claude

    assert "fixture_scenario" not in inspect.getsource(claude)


# --- fixtures validate against the same schemas used for real responses ----------------


@pytest.mark.parametrize("name", ["good", "rethink", "homemade_food"])
def test_scenario_fixtures_validate_against_production_schemas(name: str) -> None:
    pm = PreMortem.model_validate_json((FIXTURES / name / "pre_mortem.json").read_text())
    am = Assumptions.model_validate_json((FIXTURES / name / "assumptions.json").read_text())
    assert len(pm.competitors) == 3 and len(pm.failure_reasons) == 3
    assert all(f.verify for f in pm.compliance_flags)
    assert all("not legal advice" in f.note.lower() for f in pm.compliance_flags)
    assert len(am.assumptions) == 6


@pytest.mark.parametrize(
    "name,budget,verdict",
    [
        ("good", 300000, "go"),
        ("rethink", 500000, "rethink"),
        ("homemade_food", 100000, "go_with_changes"),
    ],
)
def test_scenario_fixture_verdicts_are_stable(name: str, budget: int, verdict: str) -> None:
    pm = json.loads((FIXTURES / name / "pre_mortem.json").read_text())
    am = Assumptions.model_validate_json((FIXTURES / name / "assumptions.json").read_text())
    rows = [
        Assumption(
            key=a.key, low=Decimal(str(a.low)), base=Decimal(str(a.base)),
            high=Decimal(str(a.high)), source="llm",
        )
        for a in am.assumptions
    ]  # fmt: skip
    inputs, _ = analysis.finance_inputs(analysis._values(rows), Decimal(budget))
    columns, details = analysis.compute_details(
        inputs, "founder", pm["risk_severities"], seed=7, rows=rows, pivots=pm["suggested_pivots"]
    )
    assert columns["verdict"] == verdict


def test_frontend_samples_match_the_stored_fixture_ideas_exactly() -> None:
    """The sample buttons must submit the exact stored idea and budget, or mock scenarios miss."""
    samples = json.loads(
        (Path(config.FIXTURES_DIR).parent / "frontend/src/lib/samples.json").read_text()
    )
    assert [s["id"] for s in samples] == ["good", "rethink", "homemade_food"]
    for s in samples:
        stored = json.loads((Path(config.FIXTURES_DIR) / "ideas" / f"{s['id']}.json").read_text())
        assert (s["idea"], s["starting_budget"]) == (stored["idea"], stored["starting_budget"])
        assert scenario_for_idea(s["idea"]) == s["id"]
