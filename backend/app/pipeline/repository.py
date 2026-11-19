"""Persistence helpers for businesses. No transition rules live here (see status.py)."""

import logging
import uuid
from collections.abc import Iterable
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app.models import AuditLog, Business, BusinessStatus
from app.pipeline.status import TERMINAL, Actor, record_creation

logger = logging.getLogger(__name__)


def create_business(
    session: Session,
    *,
    idea_text: str,
    market: str,
    price_guess: Decimal | None,
    starting_budget: Decimal | None,
    actor: Actor,
) -> Business:
    """Insert a business in INTAKE plus its initial audit entry. Does not commit."""
    business = Business(
        idea_text=idea_text,
        market=market,
        price_guess=price_guess,
        starting_budget=starting_budget,
        status=BusinessStatus.INTAKE,
    )
    session.add(business)
    session.flush()
    record_creation(session, business, actor)
    return business


def get_business(session: Session, business_id: uuid.UUID) -> Business | None:
    return session.get(Business, business_id)


def list_audit(session: Session, business_id: uuid.UUID) -> list[AuditLog]:
    return list(
        session.scalars(
            select(AuditLog).where(AuditLog.business_id == business_id).order_by(AuditLog.id)
        )
    )


def claim_next(
    session: Session,
    worker_id: str,
    ttl_seconds: int,
    exclude: Iterable[BusinessStatus] = (),
) -> Business | None:
    """Atomically lease one runnable business, or None.

    Runnable = non-terminal, not FAILED, and unleased (or lease expired). FOR UPDATE
    SKIP LOCKED means concurrent workers never pick the same row; the lease means a
    crashed worker's business becomes claimable again once it expires. `exclude` lists
    statuses with no handler (deliberate stop points) so they are never claimed. Commits.
    """
    skip = [BusinessStatus.FAILED.value, *(s.value for s in TERMINAL), *(s.value for s in exclude)]
    candidate = (
        select(Business.id)
        .where(
            Business.status.not_in(skip),
            (Business.locked_until.is_(None)) | (Business.locked_until < func.now()),
        )
        .order_by(Business.created_at, Business.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    claimed_id = session.execute(
        update(Business)
        .where(Business.id == candidate)
        .values(locked_by=worker_id, locked_until=func.now() + timedelta(seconds=ttl_seconds))
        .returning(Business.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    session.commit()
    if claimed_id is None:
        return None
    return session.get(Business, claimed_id, populate_existing=True)


def renew_lease(session: Session, business_id: uuid.UUID, worker_id: str, ttl_seconds: int) -> bool:
    """Extend our lease. Returns False if we no longer hold it. Commits."""
    renewed = session.execute(
        update(Business)
        .where(
            Business.id == business_id,
            Business.locked_by == worker_id,
            Business.locked_until > func.now(),
        )
        .values(locked_until=func.now() + timedelta(seconds=ttl_seconds))
        .returning(Business.id)
    ).scalar_one_or_none()
    session.commit()
    return renewed is not None


def release_lease(session: Session, business_id: uuid.UUID, worker_id: str) -> None:
    """Drop our lease if we still hold it. Commits."""
    session.execute(
        update(Business)
        .where(Business.id == business_id, Business.locked_by == worker_id)
        .values(locked_by=None, locked_until=None)
    )
    session.commit()


def release_lease_quietly(
    session_factory: sessionmaker[Session], business_id: uuid.UUID, worker_id: str
) -> None:
    """Best-effort release in a fresh session; an expiring lease covers any failure here."""
    try:
        with session_factory() as session:
            release_lease(session, business_id, worker_id)
    except Exception:
        logger.warning("business_id=%s could not release lease", business_id, exc_info=True)
