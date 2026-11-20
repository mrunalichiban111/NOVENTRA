"""Persona panel diversity rules, Haiku-only routing and deterministic aggregation. Pure."""

import copy
import json

import pytest
from pydantic import ValidationError

from app import config
from app.agents import personas
from app.llm.pricing import PRICES
from app.llm.routing import TASKS
from app.llm.schemas import PersonaPanelOut
from app.pipeline.arena import aggregate

with open(f"{config.FIXTURES_DIR}/llm/persona_panel.json", encoding="utf-8") as fh:
    PANEL = json.load(fh)


def test_fixture_panel_is_valid_and_has_30() -> None:
    assert len(PersonaPanelOut.model_validate(PANEL).personas) == 30


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda ps: ps.pop(), "at least 30"),
        (lambda ps: [p.update(skepticism=1) for p in ps], "skepticism"),
        (lambda ps: [p.update(fit="target") for p in ps], "weak"),
        (lambda ps: [p.update(budget_tier_inr="2k_5k") for p in ps], "budget_tier_inr"),
        (lambda ps: ps[1].update(name=ps[0]["name"]), "unique"),
        (lambda ps: ps[0].update(urgency=9), "less than or equal to 5"),
    ],
)
def test_diversity_and_range_violations_are_rejected(mutate, message: str) -> None:  # noqa: ANN001
    data = copy.deepcopy(PANEL)
    mutate(data["personas"])
    with pytest.raises(ValidationError, match=message):
        PersonaPanelOut.model_validate(data)


def test_evaluation_routes_to_haiku_only(monkeypatch: pytest.MonkeyPatch) -> None:
    assert TASKS["persona_evaluation"].tier == "small"
    assert "claude-haiku-5-5" in PRICES
    monkeypatch.setattr(config, "MOCK_LLM", False)
    monkeypatch.setattr(config, "CLAUDE_MODEL_SMALL", "claude-haiku-5-5")
    assert personas.assert_haiku() == "claude-haiku-5-5"
    for wrong in ("claude-sonnet-5-5", "gemini-2.5-flash", ""):
        monkeypatch.setattr(config, "CLAUDE_MODEL_SMALL", wrong)
        with pytest.raises(Exception) as exc:
            personas.assert_haiku()
        assert type(exc.value).__name__ in {"EvaluatorMisconfigured", "ConfigError"}


def test_no_gemini_provider_exists() -> None:
    from pathlib import Path

    import app.llm as llm_pkg

    names = " ".join(p.name for p in Path(llm_pkg.__file__).parent.glob("*.py"))
    assert "gemini" not in names.lower()


def _run(pid: str, rep: int, decision: str, otype: str, section: str, obj: str) -> dict:
    return {"persona_id": pid, "run_idx": rep, "decision": decision, "objection_type": otype,
            "objection": obj, "section": section, "missing_info": ["Menu"] if otype == "missing_info" else [],
            "reasoning": "r"}  # fmt: skip


def test_aggregation_is_deterministic_and_groups_issues() -> None:
    ps = [{"id": "p01", "name": "A"}, {"id": "p02", "name": "B"}]
    runs = [
        _run("p01", 0, "bounce", "missing_info", "pricing", "No menu"),
        _run("p01", 1, "bounce", "missing_info", "pricing", "No menu"),
        _run("p02", 0, "convert", "trust", "proof", "Who cooks"),
        _run("p02", 1, "bounce", "missing_info", "pricing", "Menu?"),
    ]
    a = aggregate(runs, ps, 2)
    b = aggregate(list(reversed(runs)), ps, 2)
    assert a == b
    top = a["objections"][0]
    assert (top["objection_type"], top["section"], top["count"], top["personas"]) == (
        "missing_info", "pricing", 3, 2,
    )  # fmt: skip
    assert top["example"] == "No menu"
    assert a["complete"] is True and a["converted"] == 1 and a["simulated"] is True
    assert a["missing_info"] == [{"item": "Menu", "count": 3}]
    assert aggregate(runs[:3], ps, 2)["complete"] is False
