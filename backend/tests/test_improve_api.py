"""M6 improvement routes (FR-IM-1..3, FR-IM-6). *_test DB, MOCK_LLM fixtures, no network."""

import uuid

import pytest

from app.pipeline import arena
from tests.arena.helpers import FakeArenaLLM, configure, live_business

W = "w-improve-api"


@pytest.fixture(autouse=True)
def _cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)


def test_improve_routes_happy_path_and_errors(client, session_factory) -> None:
    assert client.get(f"/api/businesses/{uuid.uuid4()}/improve").status_code == 404
    bid = live_business(session_factory)
    base = f"/api/businesses/{bid}"
    assert client.get(f"{base}/improve").json()["status"] == "none"
    r = client.post(f"{base}/improve/start")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "evaluation_incomplete"

    llm = FakeArenaLLM()
    client.post(f"{base}/personas/generate")
    arena.work_once(session_factory, W, llm)
    client.post(f"{base}/personas/evaluate")
    arena.work_once(session_factory, W, llm)

    r = client.post(f"{base}/improve/start")
    assert r.status_code == 201
    body = r.json()
    assert body["simulated"] is True and body["opportunities"] and body["original"]["d_score"]
    assert client.post(f"{base}/improve/start").status_code == 200  # idempotent

    r = client.post(f"{base}/improve/proposal")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "proposal_not_ready"
    for q in body["questions"]:
        r = client.patch(f"{base}/improve/questions/{q['id']}", json={"not_decided": True})
        assert r.status_code == 200
    assert client.patch(f"{base}/improve/questions/zzz", json={"answer": "x"}).status_code == 404

    r = client.post(f"{base}/improve/proposal")
    assert r.status_code == 200 and r.json()["status"] == "proposed"
    items = r.json()["proposal"]["items"]
    assert items and all(i["old"] != i["new"] for i in items)  # per-field diff
    r = client.post(f"{base}/improve/promote")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "evaluation_in_progress"
    r = client.post(f"{base}/improve/approve", json={"item_ids": [items[0]["id"]]})
    assert r.status_code == 202 and r.json()["status"] == "approved_for_draft"
    r = client.post(f"{base}/site/rollback", json={"to_version": 5})
    assert r.status_code == 422
