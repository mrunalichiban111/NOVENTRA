"""Website content agent: persisted facts only, one call, mock fixtures. No DB, no network."""

import uuid
from decimal import Decimal
from typing import Any

import pytest

from app.agents import site_content
from app.agents.site_content import SiteInputMissing, content_prompt, generate_site_content
from app.llm.schemas import WebsiteContent
from app.llm.structured import StructuredResult
from app.models import Business
from tests.llm_helpers import valid_for


def business(**kw: Any) -> Business:
    defaults = {
        "id": uuid.uuid4(),
        "idea_text": "Home-cooked tiffins for hostel students in Pune.",
        "market": "India",
        "price_guess": Decimal("2499"),
        "target_customer": "fallback customer",
        "premortem_json": {
            "target_customer": "Hostel students in Pune",
            "competitors": [{"name": "Swiggy"}, {"name": "Local mess"}, {"name": "Zomato"}],
        },
    }
    return Business(**(defaults | kw))


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(self, task: str, schema: type, prompt: str, **kw: Any) -> StructuredResult:
        self.calls.append({"task": task, "schema": schema, "prompt": prompt, **kw})
        return StructuredResult(
            data=WebsiteContent.model_validate(valid_for("website_content")),
            model="fake",
            provider="fake",
        )


def test_prompt_uses_only_persisted_facts() -> None:
    b = business()
    prompt = content_prompt(b, "Tiffo")
    assert prompt.splitlines() == [
        "Business name: Tiffo",
        "Market: India",
        "Target customer: Hostel students in Pune",
        "Founder's price: stated (the system will show it)",
        "Idea: Home-cooked tiffins for hostel students in Pune.",
    ]
    # competitors are never passed (the page must not name them); the price value is not
    # passed either: Python renders it, so the model cannot restate or alter it
    assert "Swiggy" not in prompt and "2499" not in prompt


def test_prompt_marks_unknown_facts_as_not_stated() -> None:
    b = business(price_guess=None, premortem_json=None, target_customer=None)
    prompt = content_prompt(b, "Tiffo")
    assert "Target customer: not stated" in prompt
    assert "Founder's price: not stated" in prompt


def test_generate_calls_the_model_once_with_run_and_business_ids() -> None:
    b, llm = business(), FakeLLM()
    out = generate_site_content(b, business_name=" Tiffo ", run_id="run-7", run_llm=llm)
    assert isinstance(out, WebsiteContent)
    (call,) = llm.calls
    assert call["task"] == "website_content" and call["schema"] is WebsiteContent
    assert call["run_id"] == "run-7" and call["business_id"] == b.id
    assert call["prompt"].startswith("Business name: Tiffo\n")
    assert call["fixture_scenario"] is None  # unknown idea -> default fixture


@pytest.mark.parametrize(("kw", "name"), [({}, ""), ({}, "   "), ({"idea_text": "  "}, "Tiffo")])
def test_missing_inputs_refused_before_any_call(kw: dict, name: str) -> None:
    llm = FakeLLM()
    with pytest.raises(SiteInputMissing):
        generate_site_content(business(**kw), business_name=name, run_id="r", run_llm=llm)
    assert llm.calls == []


def test_mock_mode_returns_the_recorded_fixture() -> None:
    # Real run_structured in MOCK_LLM mode (set by conftest): MockProvider, no network.
    out = generate_site_content(business(), business_name="Tiffo", run_id="r")
    assert out.hero.headline.startswith("Same-day delivery")


def test_scenario_fixture_only_used_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(site_content, "scenario_for_idea", lambda _idea: "good")
    # fixtures/llm/good/ has no content.json, so the default fixture is used
    assert site_content._scenario(business()) is None
