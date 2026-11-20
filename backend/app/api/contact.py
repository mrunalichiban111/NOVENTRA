"""Public contact form submission and SMTP delivery."""

import ipaddress
import logging
import smtplib
import uuid
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import text
from sqlalchemy.orm import Session

from app import config
from app.api.schemas import ContactCreate
from app.database import get_session
from app.models import ContactMessage

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api", tags=["public"])
SessionDep = Annotated[Session, Depends(get_session)]


def _client_ip(request: Request) -> str:
    peer = request.client.host if request.client else "unknown"
    trusted = False
    try:
        peer_ip = ipaddress.ip_address(peer)
        trusted = any(
            peer_ip == ipaddress.ip_address(value)
            or peer_ip in ipaddress.ip_network(value, strict=False)
            for value in config.TRUSTED_PROXY_IPS
        )
    except ValueError:
        pass
    if trusted:
        forwarded = request.headers.get("x-forwarded-for", "")
        candidate = forwarded.split(",", 1)[0].strip()
        try:
            return str(ipaddress.ip_address(candidate))
        except ValueError:
            pass
    try:
        return str(ipaddress.ip_address(peer))
    except ValueError:
        return peer[:45] or "unknown"


def _smtp_message(payload: ContactCreate) -> EmailMessage:
    if not config.CONTACT_TO or not config.CONTACT_FROM or not config.SMTP_HOST:
        raise RuntimeError("SMTP contact configuration is incomplete")
    if config.SMTP_USE_SSL and config.SMTP_STARTTLS:
        raise RuntimeError("SMTP_USE_SSL and SMTP_STARTTLS cannot both be enabled")
    message = EmailMessage()
    message["From"] = config.CONTACT_FROM
    message["To"] = config.CONTACT_TO
    message["Reply-To"] = str(payload.email)
    message["Subject"] = f"[NOVENTRA contact] {payload.name}"
    message.set_content(f"Name: {payload.name}\nEmail: {payload.email}\n\n{payload.message}")
    return message


def _send(payload: ContactCreate) -> None:
    message = _smtp_message(payload)
    if config.SMTP_USE_SSL:
        with smtplib.SMTP_SSL(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as smtp:
            if config.SMTP_USER:
                smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
            smtp.send_message(message)
        return
    with smtplib.SMTP(config.SMTP_HOST, config.SMTP_PORT, timeout=10) as smtp:
        smtp.ehlo()
        if config.SMTP_STARTTLS:
            smtp.starttls()
            smtp.ehlo()
        if config.SMTP_USER:
            smtp.login(config.SMTP_USER, config.SMTP_PASSWORD)
        smtp.send_message(message)


def submit_contact(payload: ContactCreate, request: Request, session: Session) -> dict[str, str]:
    if payload.website:
        return {"status": "ok"}

    request_id = uuid.uuid4().hex
    ip = _client_ip(request)
    now = datetime.now(UTC)
    hour_ago = now - timedelta(hours=1)
    day_ago = now - timedelta(days=1)
    with session.begin():
        session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:ip))"), {"ip": ip})
        hour_count = session.execute(
            text("SELECT count(*) FROM contact_messages WHERE ip = :ip AND created_at >= :since"),
            {"ip": ip, "since": hour_ago},
        ).scalar_one()
        day_count = session.execute(
            text("SELECT count(*) FROM contact_messages WHERE ip = :ip AND created_at >= :since"),
            {"ip": ip, "since": day_ago},
        ).scalar_one()
        if hour_count >= 3 or day_count >= 10:
            raise HTTPException(
                status_code=429, detail="Too many contact requests. Please try again later."
            )
        row = ContactMessage(
            name=payload.name,
            email=str(payload.email),
            message=payload.message,
            ip=ip,
            sent_ok=False,
        )
        session.add(row)
    try:
        _send(payload)
    except Exception:
        logger.exception(
            "contact email delivery failed request_id=%s",
            request_id,
        )
        return _mark_sent(session, row, False)
    return _mark_sent(session, row, True)


def _mark_sent(session: Session, row: ContactMessage, sent_ok: bool) -> dict[str, str]:
    row.sent_ok = sent_ok
    session.commit()
    if not sent_ok:
        raise HTTPException(status_code=502, detail="Couldn't send right now, please try again")
    return {"status": "ok"}


@router.post("/public/contact")
def contact(payload: ContactCreate, request: Request, session: SessionDep) -> dict[str, str]:
    return submit_contact(payload, request, session)
