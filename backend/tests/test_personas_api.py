"""Persona arena routes (FR-AR-1..4, FR-AR-7, FR-AR-8). *_test DB, MOCK_LLM fixtures, no network.
The worker step uses the real run_structured + MockProvider path (recorded fixtures)."""

import uuid

import pytest

from app import config
from app.api import ratelimit
from app.pipeline import arena
from tests.arena.helpers import configure, live_business

W = "w-api"


@pytest.fixture(autouse=True)
def _cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)


def base(bid) -> str:  # noqa: ANN001
    return f"/api/businesses/{bid}/personas"


def test_happy_path_with_mock_fixtures(client, session_factory) -> None:
    bid = live_business(session_factory)
    assert client.get(base(bid)).json()["status"] == "none"
    assert client.get(f"{base(bid)}/results").json()["status"] == "not_started"
    # Evaluate before the panel exists.
    r = client.post(f"{base(bid)}/evaluate")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "personas_not_ready"

    r = client.post(f"{base(bid)}/generate")
    assert r.status_code == 202 and r.json()["status"] == "pending" and r.json()["simulated"]
    assert client.post(f"{base(bid)}/generate").status_code == 200  # idempotent
    assert arena.work_once(session_factory, W)  # real mock-provider path
    panel = client.get(base(bid)).json()
    assert panel["status"] == "ready" and panel["count"] == 30
    assert panel["label"] == "Simulated results, not market research"

    r = client.post(f"{base(bid)}/evaluate", json={})
    assert r.status_code == 202 and r.json()["site_version"] == 1
    running = client.get(f"{base(bid)}/results").json()
    assert running["status"] == "queued" and running["d_score"] is None
    assert len(running["personas"]) == 30 and running["completed_runs"] == 0

    assert arena.work_once(session_factory, W)
    res = client.get(f"{base(bid)}/results").json()
    assert res["status"] == "complete" and res["completed_runs"] == 90
    assert res["d_score"] == pytest.approx(33.33, abs=0.01)  # fixture: 10 of 30 convert
    assert res["d_se"] is not None and "noise" in res["note"]
    assert res["simulated"] is True and res["objections"]
    assert res["comparable_key"]["rubric_version"] == "d-v1"
    assert client.post(f"{base(bid)}/evaluate").status_code == 200  # complete: no-op


def test_errors(client, session_factory) -> None:
    missing = uuid.uuid4()
    assert client.get(base(missing)).status_code == 404
    assert client.post(f"{base(missing)}/generate").status_code == 404

    bid = live_business(session_factory)
    client.post(f"{base(bid)}/generate")
    arena.work_once(session_factory, W)
    r = client.post(f"{base(bid)}/evaluate", json={"site_version": 9})
    assert r.status_code == 422 and r.json()["detail"]["code"] == "invalid_version"


def test_evaluator_must_be_haiku_and_daily_precheck(client, session_factory, monkeypatch) -> None:
    bid = live_business(session_factory)
    monkeypatch.setattr(config, "MOCK_LLM", False)
    monkeypatch.setattr(config, "CLAUDE_MODEL_SMALL", "claude-sonnet-5-5")
    r = client.post(f"{base(bid)}/evaluate")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "evaluator_misconfigured"
    monkeypatch.setattr(config, "DAILY_SPEND_CAP_USD", 0.0)
    r = client.post(f"{base(bid)}/generate")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "budget_exceeded"


def test_generate_is_rate_limited(client, session_factory) -> None:
    bid = live_business(session_factory)
    ratelimit.limiter.reset()
    ratelimit.limiter.limit = 2
    try:
        codes = [client.post(f"{base(bid)}/generate").status_code for _ in range(3)]
    finally:
        ratelimit.limiter.limit = ratelimit.RATE_LIMIT_PER_MINUTE
    assert codes == [202, 200, 429]
