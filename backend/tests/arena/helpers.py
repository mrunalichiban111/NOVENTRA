"""Arena test helpers: a LIVE business with a real (mock-provider) deployed release, and a fake
LLM that records prompts, measures concurrency and can fail chosen persona calls."""

import json
import threading
import time
import uuid
from typing import Any

import pytest
from sqlalchemy.orm import Session, sessionmaker

from app import config
from app.deploy.providers import MockDeployProvider
from app.deploy.site_model import SiteLinks
from app.llm.base import TransportError
from app.llm.schemas import PersonaEvaluationBatch, PersonaPanelOut
from app.llm.structured import StructuredResult
from app.pipeline import repository
from app.pipeline.handlers import pipeline_stages
from app.pipeline.runner import run_business
from app.pipeline.status import RETRYABLE_STAGES
from tests.m3_helpers import SpyLLM
from tests.test_deploy_pipeline import BASE, pending_business, request

FIXTURES = config.FIXTURES_DIR


def configure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "BASE_DOMAIN", BASE)
    monkeypatch.setattr(config, "DEPLOY_PROVIDER", "mock")
    monkeypatch.setattr(config, "APP_ENV", "development")
    monkeypatch.setattr(config, "SITE_REQUIRE_PUBLIC", False)
    monkeypatch.setattr(config, "SITE_DNS_MODE", "skip")
    monkeypatch.setattr(config, "SITE_HTTP_CHECK", False)
    monkeypatch.setattr(config, "WAITLIST_POST_URL", "")
    monkeypatch.setattr(config, "ABUSE_REPORT_EMAIL", "")


def live_business(sf: sessionmaker[Session]) -> uuid.UUID:
    """DOMAIN_PENDING -> deploy (mock provider, mock LLM) -> LIVE with site v1 deployed."""
    bid = pending_business(sf)
    request(sf, bid)
    provider = MockDeployProvider()
    stages = pipeline_stages(SpyLLM(), provider_factory=lambda: provider, links=SiteLinks())
    with sf() as s:
        repository.claim_next(s, "w-deploy", 60, exclude=RETRYABLE_STAGES - stages.keys())
    run_business(sf, bid, worker_id="w-deploy", stages=stages)
    return bid


def _panel_data() -> dict[str, Any]:
    with open(f"{FIXTURES}/llm/persona_panel.json", encoding="utf-8") as fh:
        return json.load(fh)


def persona_ids(prompt: str) -> list[str]:
    block = prompt.split("PERSONAS (evaluate each of these ids exactly once):\n", 1)[1]
    return [p["id"] for p in json.loads(block)]


class FakeArenaLLM:
    """Stands in for run_structured. Decision: convert iff persona number % 3 == 0 (10/30)."""

    def __init__(
        self,
        fail: set[tuple[str, ...]] | None = None,
        delay: float = 0.02,
        reward_marker: str | None = None,
        panel_size: int = 30,
    ) -> None:
        self.panel_size = panel_size  # number of fixture personas the fake panel call returns
        self.reward_marker = reward_marker  # page text that makes even personas convert too
        self.calls: list[dict[str, Any]] = []
        self.fail = fail or set()  # frozensets of persona-id tuples to fail
        self.delay = delay
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def tasks(self) -> list[str]:
        return [c["task"] for c in self.calls]

    def __call__(self, task: str, schema: Any, prompt: str, **kw: Any) -> StructuredResult:
        with self._lock:
            self.calls.append({"task": task, "prompt": prompt, "run_id": kw.get("run_id")})
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            time.sleep(self.delay)
            if task == "persona_panel":
                raw = _panel_data()
                raw["personas"] = raw["personas"][-self.panel_size :]  # keeps the weak tail
                data = PersonaPanelOut.model_validate(raw)
                return StructuredResult(data=data, model="fake-strong", provider="fake")
            ids = persona_ids(prompt)
            if tuple(ids) in self.fail:
                raise TransportError("simulated 529", retryable=False, status_code=529)
            rewarded = bool(self.reward_marker and self.reward_marker in prompt)

            def converts(pid: str) -> bool:
                n = int(pid[1:])
                return n % 3 == 0 or (rewarded and n % 2 == 0)

            evals = [
                {
                    "persona_id": pid,
                    "decision": "convert" if converts(pid) else "bounce",
                    "confidence": 0.7,
                    "objection_type": "price" if int(pid[1:]) % 2 else "trust",
                    "top_objection": "Price unclear" if int(pid[1:]) % 2 else "Who cooks?",
                    "section": "pricing" if int(pid[1:]) % 2 else "proof",
                    "missing_info": ["Price per meal"] if int(pid[1:]) % 2 else [],
                    "reason": "Test reason.",
                }
                for pid in ids
            ]
            data = PersonaEvaluationBatch.model_validate({"evaluations": evals})
            return StructuredResult(data=data, model="claude-haiku-5-5", provider="fake")
        finally:
            with self._lock:
                self.active -= 1
