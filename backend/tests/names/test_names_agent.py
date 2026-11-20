"""Names agent in mock mode: no key, no network, no DB (FR-BR-1). Uses the recorded fixtures."""

import json
import uuid
from pathlib import Path

import pytest

from app import config
from app.agents import names as agent
from app.models import Business
from app.names import config as c

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "fixtures"


@pytest.fixture(autouse=True)
def _mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "MOCK_LLM", True)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")
    monkeypatch.setattr(config, "FIXTURES_DIR", str(FIXTURES))
    monkeypatch.setattr(config, "BASE_DOMAIN", "")


def business(idea_file: str | None = None, premortem: dict | None = None) -> Business:
    idea = (
        json.loads((FIXTURES / "ideas" / f"{idea_file}.json").read_text())["idea"]
        if idea_file
        else "A neighbourhood delivery idea for kirana shops."
    )
    pm = (
        premortem
        if premortem is not None
        else {"target_customer": "Shop owners", "competitors": []}
    )
    return Business(id=uuid.uuid4(), idea_text=idea, market="India", premortem_json=pm)


def test_good_scenario_fixture_exercises_validation() -> None:
    out = agent.generate_names(business("good"), run_id="names-test")
    reasons = {r.invalid_reason for r in out if not r.valid}
    assert {
        c.UNSUPPORTED_CHAR, c.PUNYCODE, c.EDGE_HYPHEN, c.DOUBLE_HYPHEN,
        c.TOO_SHORT, c.RESERVED, c.DUPLICATE_SLUG,
    } <= reasons  # fmt: skip
    valid = [r for r in out if r.valid]
    assert len(valid) >= 30 and len(out) == 41  # one case-duplicate dropped by normalization
    assert [r.rank for r in valid] == list(range(1, len(valid) + 1))
    assert "FILEWISE" not in {r.name for r in out}
    assert {"Ledger Lane", "Café Ledger"} <= {r.name for r in valid}


def test_apex_label_comes_from_base_domain(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "BASE_DOMAIN", "taxnest.in")
    out = {r.name: r for r in agent.generate_names(business("good"), run_id="names-test")}
    assert out["Taxnest"].invalid_reason == c.APEX_LABEL
    # the accented twin has the same slug, so it is the apex label too (not a duplicate)
    assert out["Taxnëst"].invalid_reason == c.APEX_LABEL


def test_unknown_idea_uses_default_fixture() -> None:
    out = agent.generate_names(business(), run_id="names-test")
    assert len(out) >= 40 and all(r.valid for r in out)


def test_scenario_without_names_fixture_falls_back_to_default() -> None:
    out = agent.generate_names(business("homemade_food"), run_id="names-test")
    assert len(out) >= 40


def test_prompt_uses_stored_data_and_is_untrusted() -> None:
    b = business(premortem={"target_customer": "Freelancers", "competitors": ["CA firms"]})
    prompt = agent.names_prompt(b)
    assert "Target customer: Freelancers" in prompt and "- CA firms" in prompt
    assert "Market: India" in prompt and b.idea_text in prompt

    seen = {}

    def fake(task, schema, prompt, **kw):  # noqa: ANN001, ANN003, ANN202
        seen.update(task=task, prompt=prompt, **kw)
        from app.llm.structured import run_structured

        return run_structured(task, schema, prompt, **kw)

    agent.generate_names(b, run_id="names-abc", run_llm=fake)
    assert seen["task"] == "business_names" and seen["run_id"] == "names-abc"
    assert seen["business_id"] == b.id


def test_missing_premortem_is_refused() -> None:
    with pytest.raises(agent.NamesInputMissing):
        agent.generate_names(business(premortem={}), run_id="names-test")
