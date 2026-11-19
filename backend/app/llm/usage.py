"""Usage persistence (llm_usage). Costs are ESTIMATES from app/llm/pricing.py, not billing.

Every function opens its own session/transaction (via `session_factory`), never the
caller's, so a usage row survives a later pipeline-stage rollback.
"""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import LlmUsage

SessionFactory = sessionmaker[Session]


def default_session_factory() -> SessionFactory:
    from app.database import SessionLocal

    return SessionLocal


def to_usd(value: float | Decimal) -> Decimal:
    return Decimal(str(value)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP)


def add_usage(session: Session, **fields: Any) -> LlmUsage:
    """Stage a usage row on `session` (caller commits). `estimated_cost_usd` may be float."""
    fields["estimated_cost_usd"] = to_usd(fields.get("estimated_cost_usd", 0))
    row = LlmUsage(id=uuid.uuid4(), **fields)
    session.add(row)
    return row


def spend_for_run(session: Session, run_id: str) -> Decimal:
    total = session.scalar(
        select(func.coalesce(func.sum(LlmUsage.estimated_cost_usd), 0)).where(
            LlmUsage.run_id == run_id
        )
    )
    return Decimal(total or 0)


def spend_last_24h_usd(session_factory: SessionFactory | None = None) -> Decimal:
    """Estimated spend over the last 24 hours. M7's daily cap is a one-liner on top of this."""
    factory = session_factory or default_session_factory()
    since = datetime.now(UTC) - timedelta(hours=24)
    with factory() as session:
        total = session.scalar(
            select(func.coalesce(func.sum(LlmUsage.estimated_cost_usd), 0)).where(
                LlmUsage.created_at >= since
            )
        )
    return Decimal(total or 0)
