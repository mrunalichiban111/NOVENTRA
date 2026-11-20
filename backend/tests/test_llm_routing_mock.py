"""Routing table and MockProvider. No DB, no network."""

import socket

import pytest

from app import config
from app.llm import routing, structured
from app.llm.base import ConfigError, LLMRequest
from app.llm.mock import MockProvider
from app.llm.schemas import OUTPUT_SCHEMAS
from tests.llm_helpers import valid_for


def test_task_table_has_exactly_the_m2_tasks():
    assert set(routing.TASKS) == {
        "premortem",
        "assumption_extraction",
        "business_names",
        "website_content",
        "persona_panel",  # M5
        "persona_evaluation",  # M5, Claude Haiku only (tier small)
    }
    assert routing.TASKS["persona_evaluation"].tier == "small"
    assert set(routing.TASKS) == set(OUTPUT_SCHEMAS)
    for spec in routing.TASKS.values():
        assert spec.max_tokens > 0 and spec.timeout_s > 0 and spec.tier in ("strong", "small")


def test_model_ids_come_only_from_config(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "CLAUDE_MODEL_STRONG", "strong-x")
    monkeypatch.setattr(config, "CLAUDE_MODEL_SMALL", "small-y")
    assert routing.model_for(routing.get_task("premortem")) == "strong-x"
    assert routing.model_for(routing.get_task("business_names")) == "small-y"


def test_missing_model_config_and_unknown_task_raise(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(config, "CLAUDE_MODEL_STRONG", "")
    with pytest.raises(ConfigError):
        routing.model_for(routing.get_task("premortem"))
    with pytest.raises(ConfigError):
        routing.get_task("nope")


@pytest.fixture
def fixtures_dir(tmp_path):
    d = tmp_path / "llm"
    d.mkdir()
    import json

    for task, spec in routing.TASKS.items():
        (d / spec.fixture).write_text(json.dumps(valid_for(task)))
    return tmp_path


def test_mock_provider_makes_zero_network_calls(fixtures_dir, monkeypatch: pytest.MonkeyPatch):
    def boom(*a, **k):
        raise AssertionError("network access attempted")

    monkeypatch.setattr(socket.socket, "connect", boom)
    monkeypatch.setattr(socket, "create_connection", boom)
    monkeypatch.setattr(socket, "getaddrinfo", boom)
    monkeypatch.setattr(config, "ANTHROPIC_API_KEY", "")  # no key at all
    monkeypatch.setattr(config, "MOCK_LLM", True)
    monkeypatch.setattr(config, "FIXTURES_DIR", str(fixtures_dir))
    for task, schema in OUTPUT_SCHEMAS.items():
        result = structured.run_structured(task, schema, "idea text")
        assert result.provider == "mock" and result.model == "mock"
        assert all(a.response.input_tokens == 0 for a in result.attempts)


def test_mock_is_deterministic(fixtures_dir):
    p = MockProvider(fixtures_dir)
    req = LLMRequest("premortem", "mock", "s", "u", 10, 1.0, tool_name="t")
    assert p.complete(req) == p.complete(req)


def test_mock_text_mode_and_missing_fixture(tmp_path, fixtures_dir):
    req = LLMRequest("premortem", "mock", "s", "u", 10, 1.0)
    assert MockProvider(fixtures_dir).complete(req).text.startswith("{")
    with pytest.raises(ConfigError):
        MockProvider(tmp_path / "empty").complete(req)
