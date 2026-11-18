import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import ratelimit
from app.models import AuditLog, Business
from app.pipeline import status as sm
from app.pipeline.status import Actor

IDEA = "I want to sell monthly artisanal tea boxes. Customers pick blends online. We ship across India."


def create(client: TestClient, **extra: object):
    return client.post("/api/businesses", json={"idea_text": IDEA, **extra})


def test_create_minimal_defaults_market_and_intake(client: TestClient, session: Session) -> None:
    r = create(client)
    assert r.status_code == 201
    body = r.json()
    assert body["status"] == "INTAKE"
    bid = uuid.UUID(body["id"])
    b = session.get(Business, bid)
    assert (b.market, b.price_guess, b.starting_budget) == ("India", None, None)


def test_create_with_optional_fields_and_initial_audit(
    client: TestClient, session: Session
) -> None:
    r = create(client, market="Germany", price_guess="499.50", starting_budget=100000)
    bid = uuid.UUID(r.json()["id"])
    b = session.get(Business, bid)
    assert (b.market, float(b.price_guess), float(b.starting_budget)) == (
        "Germany",
        499.5,
        100000.0,
    )
    entries = list(session.scalars(select(AuditLog).where(AuditLog.business_id == bid)))
    assert [(e.action, e.actor, e.to_status) for e in entries] == [
        ("business.created", "user", "INTAKE")
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"idea_text": ""},
        {"idea_text": "Only one sentence."},
        {"idea_text": "One. Two. Three. Four. Five. Six."},
        {"idea_text": "x" * 2001},
        {"idea_text": IDEA, "price_guess": -1},
        {"idea_text": IDEA, "starting_budget": "abc"},
        {"idea_text": IDEA, "market": "  "},
        {"idea_text": IDEA, "market": "m" * 81},
        {"idea_text": IDEA, "status": "DONE"},  # unknown fields are rejected
    ],
)
def test_invalid_create_is_422_and_stores_nothing(
    client: TestClient, session: Session, payload: dict
) -> None:
    assert client.post("/api/businesses", json=payload).status_code == 422
    assert session.scalar(select(func.count()).select_from(Business)) == 0


def test_sentence_boundaries() -> None:
    from app.api.schemas import count_sentences

    assert count_sentences("A b. C d!  E f?") == 3
    assert count_sentences("No terminator at all") == 1


def test_get_business_returns_details_and_pipeline(client: TestClient) -> None:
    bid = create(client).json()["id"]
    r = client.get(f"/api/businesses/{bid}")
    assert r.status_code == 200
    body = r.json()
    assert (body["id"], body["status"], body["market"]) == (bid, "INTAKE", "India")
    p = body["pipeline"]
    assert (p["current_stage"], p["next_stage"], p["terminal"], p["retryable"]) == (
        "INTAKE", "PREMORTEM", False, False,
    )  # fmt: skip
    assert [h["action"] for h in p["history"]] == ["business.created"]


def test_get_unknown_is_404_and_bad_id_is_422(client: TestClient) -> None:
    missing = uuid.uuid4()
    r = client.get(f"/api/businesses/{missing}")
    assert r.status_code == 404
    assert str(missing) in r.json()["detail"]
    assert client.get("/api/businesses/not-a-uuid").status_code == 422


def test_failed_business_exposes_retry_info_and_retry_works(
    client: TestClient, session: Session
) -> None:
    bid = uuid.UUID(create(client).json()["id"])
    sm.fail(session, bid, actor=Actor.worker("w"), error="stub exploded")
    session.commit()
    p = client.get(f"/api/businesses/{bid}").json()["pipeline"]
    assert (p["failed_stage"], p["last_error"], p["retryable"], p["attempts"]) == (
        "INTAKE", "stub exploded", True, 1,
    )  # fmt: skip

    r = client.post(f"/api/businesses/{bid}/retry")
    assert r.status_code == 200
    assert r.json()["status"] == "INTAKE"
    actions = [h["action"] for h in r.json()["pipeline"]["history"]]
    assert actions == ["business.created", "pipeline.failed", "pipeline.retry"]


def test_retry_errors(client: TestClient, session: Session) -> None:
    bid = uuid.UUID(create(client).json()["id"])
    assert client.post(f"/api/businesses/{bid}/retry").status_code == 409  # not failed
    assert client.post(f"/api/businesses/{uuid.uuid4()}/retry").status_code == 404

    for _ in range(3):  # exhaust the retry budget
        sm.fail(session, bid, actor=Actor.user(), error="x")
        session.commit()
        client.post(f"/api/businesses/{bid}/retry")
    r = client.post(f"/api/businesses/{bid}/retry")
    assert r.status_code == 409 and "limit" in r.json()["detail"].lower()
    assert client.get(f"/api/businesses/{bid}").json()["pipeline"]["retryable"] is False


def test_rate_limit_on_public_writes(client: TestClient) -> None:
    ratelimit.limiter.limit = 2
    try:
        codes = [create(client).status_code for _ in range(3)]
    finally:
        ratelimit.limiter.limit = ratelimit.RATE_LIMIT_PER_MINUTE
    assert codes == [201, 201, 429]
