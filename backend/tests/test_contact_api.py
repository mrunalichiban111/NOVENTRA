from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.api import contact
from app.models import ContactMessage

PAYLOAD = {
    "name": "A Founder",
    "email": "founder@example.com",
    "message": "I would like to learn more about NOVENTRA.",
    "website": "",
}


@pytest.fixture
def smtp_config(monkeypatch):
    monkeypatch.setattr(config, "SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "CONTACT_TO", "hello@noventras.biz")
    monkeypatch.setattr(config, "CONTACT_FROM", "NOVENTRA <hello@noventras.biz>")


def test_successful_delivery_builds_expected_message(
    client, session: Session, monkeypatch, smtp_config
) -> None:
    sent = []
    monkeypatch.setattr(
        contact, "_send", lambda payload: sent.append(contact._smtp_message(payload))
    )

    response = client.post("/api/public/contact", json=PAYLOAD)

    assert response.status_code == 200
    assert sent[0]["From"] == config.CONTACT_FROM
    assert sent[0]["To"] == config.CONTACT_TO
    assert sent[0]["Reply-To"] == PAYLOAD["email"]
    assert sent[0]["Subject"] == "[NOVENTRA contact] A Founder"
    assert "I would like to learn more" in sent[0].get_content()
    row = session.scalar(select(ContactMessage))
    assert row is not None and row.sent_ok is True


def test_honeypot_is_silent_and_not_stored(client, session: Session) -> None:
    response = client.post("/public/contact", json={**PAYLOAD, "website": "bot"})
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert session.scalar(select(ContactMessage)) is None


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {**PAYLOAD, "name": ""},
        {**PAYLOAD, "email": "not-an-email"},
        {**PAYLOAD, "message": "short"},
        {**PAYLOAD, "name": "bad\nname"},
        {**PAYLOAD, "email": "bad\r@example.com"},
    ],
)
def test_validation_rejects_bad_contact_payload(client, session: Session, payload) -> None:
    assert client.post("/api/public/contact", json=payload).status_code == 422
    assert session.scalar(select(ContactMessage)) is None


def test_smtp_failure_keeps_unsent_row(client, session: Session, monkeypatch, smtp_config) -> None:
    monkeypatch.setattr(
        contact, "_send", lambda payload: (_ for _ in ()).throw(RuntimeError("down"))
    )

    response = client.post("/api/public/contact", json=PAYLOAD)

    assert response.status_code == 502
    row = session.scalar(select(ContactMessage))
    assert row is not None and row.sent_ok is False


def test_hourly_and_daily_limits(client, session: Session, monkeypatch, smtp_config) -> None:
    monkeypatch.setattr(contact, "_send", lambda payload: None)
    for _ in range(3):
        assert client.post("/api/public/contact", json=PAYLOAD).status_code == 200
    assert client.post("/api/public/contact", json=PAYLOAD).status_code == 429

    session.query(ContactMessage).delete()
    session.commit()
    now = datetime.now(UTC)
    for index in range(10):
        session.add(
            ContactMessage(
                name="Old",
                email=f"old{index}@example.com",
                message="An older message for the daily window.",
                ip="testclient",
                created_at=now - timedelta(hours=2),
                sent_ok=True,
            )
        )
    session.commit()
    assert client.post("/api/public/contact", json=PAYLOAD).status_code == 429


def test_concurrent_submissions_allow_exactly_three(client, monkeypatch, smtp_config) -> None:
    monkeypatch.setattr(contact, "_send", lambda payload: None)
    with ThreadPoolExecutor(max_workers=5) as pool:
        responses = list(
            pool.map(lambda _: client.post("/api/public/contact", json=PAYLOAD), range(5))
        )
    assert sorted(response.status_code for response in responses) == [200, 200, 200, 429, 429]
