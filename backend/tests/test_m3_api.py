"""GET detail and PATCH assumptions (FR-FIN-2, FR-FIN-7, FR-SC-1/3)."""

import uuid

import pytest
from fastapi.testclient import TestClient

from app.llm.mock import MockProvider
from tests.m3_helpers import assumptions, audit, budget, idea, new_business, run, scorecards


@pytest.fixture
def gated(session_factory):
    bid = new_business(session_factory, idea("good"), budget=budget("good"))
    run(session_factory, bid)
    return bid


@pytest.fixture
def no_llm(monkeypatch):
    """Any model call after this point fails the test."""

    def boom(*_a, **_k):
        raise AssertionError("PATCH must not call a model")

    monkeypatch.setattr(MockProvider, "complete", boom)


def patch(client: TestClient, bid, *items):
    return client.patch(f"/api/businesses/{bid}/assumptions", json={"assumptions": list(items)})


def by_key(body):
    return {a["key"]: a for a in body["assumptions"]}


def test_get_contains_every_section(client: TestClient, gated) -> None:
    body = client.get(f"/api/businesses/{gated}").json()
    assert body["status"] == "GATED" and body["rejection_reason"] is None
    assert body["premortem"]["competitors"][0]["label"] == "unverified"
    assert "legal advice" in body["premortem"]["disclaimer"].lower()
    assert len(body["assumptions"]) == 6
    assert {a["source"] for a in body["assumptions"]} == {"llm"}
    sc = body["scorecard"]
    assert sc["verdict"] == "go" == body["verdict"]
    assert sc["version"] == 1 and sc["stage"] == "pre"
    assert body["verdict_explanation"] == sc["explanation"] and sc["explanation"]
    assert sc["notes"] and any("simulated" in n for n in sc["notes"])
    assert 0 <= sc["economics"] <= 100 and 0 <= sc["risk"] <= 100 and 0 <= sc["overall"] <= 100
    f = body["finance"]
    assert set(f["scenarios"]) == {"pessimistic", "base", "optimistic"}
    assert f["monte_carlo"]["simulated"] is True and f["monte_carlo"]["n"] == 2000
    assert f["starting_budget_source"] == "founder" and isinstance(f["seed"], int)
    base = f["scenarios"]["base"]
    assert {"six_month_cash", "break_even_units", "break_even_month", "runway_status"} <= set(base)


def test_get_before_analysis_has_empty_sections(client: TestClient, session_factory) -> None:
    bid = new_business(session_factory, idea("good"))
    body = client.get(f"/api/businesses/{bid}").json()
    assert body["status"] == "INTAKE"
    assert body["premortem"] is None and body["scorecard"] is None and body["finance"] is None
    assert body["assumptions"] == [] and body["verdict"] is None


def test_get_rejected_shows_reason(client: TestClient, session_factory) -> None:
    bid = new_business(session_factory, idea("scam"))
    run(session_factory, bid)
    body = client.get(f"/api/businesses/{bid}").json()
    assert body["status"] == "REJECTED"
    assert "safety" in body["rejection_reason"].lower()
    assert body["scorecard"] is None and body["verdict"] is None


def test_patch_preserves_unchanged_values_tags_user_versions_and_audits(
    client: TestClient, session_factory, gated, no_llm
) -> None:
    before = by_key(client.get(f"/api/businesses/{gated}").json())
    seed_before = scorecards(session_factory, gated)[0].details_json["seed"]

    r = patch(client, gated, {"key": "unit_price", "base": 2100})
    assert r.status_code == 200
    body = r.json()
    after = by_key(body)

    assert (after["unit_price"]["low"], after["unit_price"]["base"], after["unit_price"]["high"]) == (
        before["unit_price"]["low"], 2100, before["unit_price"]["high"],
    )  # fmt: skip
    assert after["unit_price"]["source"] == "user"
    assert after["unit_price"]["rationale"] == before["unit_price"]["rationale"]
    for key in before:
        if key != "unit_price":
            assert after[key] == before[key]  # untouched, still source llm
    assert body["scorecard"]["version"] == 2 and body["status"] == "GATED"

    cards = scorecards(session_factory, gated)
    assert [c.version for c in cards] == [1, 2]
    assert cards[1].details_json["seed"] == seed_before  # same seed: edits are comparable
    snap = {a["key"]: a["source"] for a in cards[1].details_json["assumptions"]}
    assert snap["unit_price"] == "user" and snap["startup_cost"] == "llm"

    event = [a for a in audit(session_factory, gated) if a.action == "assumptions.edited"]
    assert len(event) == 1
    e = event[0]
    assert e.actor == "user" and e.version_ref == "pre-v2"
    assert (e.from_status, e.to_status) == ("GATED", "GATED")
    assert e.details["changed"] == {"unit_price": {"base": [2000.0, 2100.0]}}
    assert e.details["scorecard_version"] == 2


def test_patch_recomputes_deterministically_without_a_model(
    client: TestClient, session_factory, gated, no_llm
) -> None:
    r = patch(
        client, gated,
        {"key": "unit_price", "low": 1, "base": 1, "high": 1},
    )  # fmt: skip
    body = r.json()
    # price 1 < variable cost 500: every order loses money, so E is far below 10 -> Rethink
    assert body["scorecard"]["economics"] < 10
    assert body["verdict"] == "rethink"
    assert body["finance"]["scenarios"]["base"]["contribution_margin"] < 0
    assert body["finance"]["monte_carlo"]["p_profitable"] < 0.1
    again = patch(client, gated, {"key": "unit_price", "base": 1}).json()
    assert again["scorecard"]["version"] == 2  # no-op edit does not create a version


def test_patch_multiple_fields_audits_each_changed_field(
    client: TestClient, session_factory, gated, no_llm
) -> None:
    patch(
        client, gated,
        {"key": "fixed_monthly_cost", "low": 10000, "high": 50000},
        {"key": "monthly_visitors", "base": 3500},
    )  # fmt: skip
    e = [a for a in audit(session_factory, gated) if a.action == "assumptions.edited"][0]
    assert e.details["changed"] == {
        "fixed_monthly_cost": {"low": [15000.0, 10000.0], "high": [40000.0, 50000.0]},
        "monthly_visitors": {"base": [3000.0, 3500.0]},
    }


@pytest.mark.parametrize(
    "item",
    [
        {"key": "unit_price", "base": 99999},  # base above existing high
        {"key": "unit_price", "low": 5000},  # low above existing base
        {"key": "unit_price", "high": 100},  # high below existing base
        {"key": "visitor_to_order_conversion", "high": 1.5},  # fraction must be <= 1
    ],
)
def test_patch_rejects_an_invalid_resulting_set_and_changes_nothing(
    client: TestClient, session_factory, gated, no_llm, item
) -> None:
    snap = [(a.key, a.low, a.base, a.high, a.source) for a in assumptions(session_factory, gated)]
    n_audit = len(audit(session_factory, gated))
    r = patch(client, gated, item)
    assert r.status_code == 422
    assert [
        (a.key, a.low, a.base, a.high, a.source) for a in assumptions(session_factory, gated)
    ] == snap
    assert len(scorecards(session_factory, gated)) == 1
    assert len(audit(session_factory, gated)) == n_audit


def test_invalid_second_item_blocks_a_valid_first_item(
    client: TestClient, session_factory, gated, no_llm
) -> None:
    r = patch(
        client, gated,
        {"key": "startup_cost", "base": 70000},  # valid on its own
        {"key": "unit_price", "base": 99999},  # invalid: base > high
    )  # fmt: skip
    assert r.status_code == 422
    assert all(a.source == "llm" for a in assumptions(session_factory, gated))


@pytest.mark.parametrize(
    "payload",
    [
        {"assumptions": []},
        {"assumptions": [{"key": "unit_price"}]},  # nothing to change
        {"assumptions": [{"key": "nope", "base": 1}]},
        {"assumptions": [{"key": "unit_price", "base": -1}]},
        {"assumptions": [{"key": "unit_price", "base": "abc"}]},
        {"assumptions": [{"key": "unit_price", "base": 1, "extra": 1}]},
        {"assumptions": [{"key": "unit_price", "base": 1}, {"key": "unit_price", "base": 2}]},
        {"extra": 1, "assumptions": [{"key": "unit_price", "base": 1}]},
        {},
    ],
)
def test_patch_payload_validation(client: TestClient, gated, payload) -> None:
    assert client.patch(f"/api/businesses/{gated}/assumptions", json=payload).status_code == 422


def test_patch_not_allowed_before_analysis_or_after_rejection(
    client: TestClient, session_factory
) -> None:
    intake = new_business(session_factory, idea("good"))
    assert patch(client, intake, {"key": "unit_price", "base": 1}).status_code == 409
    rejected = new_business(session_factory, idea("scam"))
    run(session_factory, rejected)
    assert patch(client, rejected, {"key": "unit_price", "base": 1}).status_code == 409


def test_patch_unknown_business_is_404(client: TestClient) -> None:
    r = patch(client, uuid.uuid4(), {"key": "unit_price", "base": 1})
    assert r.status_code == 404


def test_retry_endpoint_still_works_for_a_failed_business(
    client: TestClient, session_factory
) -> None:
    from app.pipeline import status
    from app.pipeline.status import Actor

    bid = new_business(session_factory, idea("good"))
    with session_factory() as s:
        status.fail(s, bid, actor=Actor.user(), error="x")
        s.commit()
    r = client.post(f"/api/businesses/{bid}/retry")
    assert r.status_code == 200 and r.json()["status"] == "INTAKE"


def test_next_stage_is_null_at_gated_and_terminal_states(
    client: TestClient, session_factory
) -> None:
    from app.pipeline.handlers import m3_stages
    from app.pipeline.status import PAUSE_STAGES

    gated = new_business(session_factory, idea("good"), budget=budget("good"))
    run(session_factory, gated)
    p = client.get(f"/api/businesses/{gated}").json()["pipeline"]
    assert (p["current_stage"], p["next_stage"], p["in_progress"]) == ("GATED", None, False)

    rejected = new_business(session_factory, idea("scam"))
    run(session_factory, rejected)
    assert client.get(f"/api/businesses/{rejected}").json()["pipeline"]["next_stage"] is None

    intake = new_business(session_factory, idea("good"))
    assert client.get(f"/api/businesses/{intake}").json()["pipeline"]["next_stage"] == "PREMORTEM"
    # pause stages never have a handler, so the worker cannot run past them
    assert not (PAUSE_STAGES & set(m3_stages()))
