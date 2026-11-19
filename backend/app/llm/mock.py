"""Deterministic fixture-driven provider. No API key, no network, cost always 0."""

import json
from pathlib import Path

from app import config
from app.llm.base import ConfigError, LLMRequest, LLMResponse
from app.llm.routing import get_task
from app.llm.scenarios import check_scenario_id


class MockProvider:
    name = "mock"

    def __init__(self, fixtures_dir: str | Path | None = None):
        self._dir = Path(fixtures_dir if fixtures_dir is not None else config.FIXTURES_DIR)

    def complete(self, request: LLMRequest) -> LLMResponse:
        base = self._dir / "llm"
        if request.fixture_scenario is not None:
            # Validated against a closed id format; never derived from user text.
            base = base / check_scenario_id(request.fixture_scenario)
        path = base / get_task(request.task).fixture
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (OSError, json.JSONDecodeError) as exc:
            raise ConfigError(f"mock fixture unreadable: {path.name}: {exc}") from exc
        if request.tool_name is not None:
            return LLMResponse(model="mock", tool_input=data, stop_reason="tool_use")
        return LLMResponse(model="mock", text=raw, stop_reason="end_turn")
