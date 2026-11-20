"""Persistence for name candidates (M4A). Callers own the transaction (no commit here).

Regeneration replaces a business's *unselected* candidates only; a selected candidate is a
subdomain claim and is never deleted here. Claim changes go through the selection service
(M4B), which is the only code allowed to set `selected`.
"""

import uuid
from collections.abc import Iterable
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import DomainCandidate
from app.names.rank import NameRecord


def replace_unselected(
    session: Session, business_id: uuid.UUID, run_id: str, records: Iterable[NameRecord]
) -> list[DomainCandidate]:
    session.execute(
        delete(DomainCandidate).where(
            DomainCandidate.business_id == business_id, DomainCandidate.selected.is_(False)
        )
    )
    rows = [
        DomainCandidate(
            business_id=business_id,
            run_id=run_id,
            name=r.name,
            slug=r.slug[:128],
            valid=r.valid,
            invalid_reason=r.invalid_reason,
            rank=r.rank,
            score_total=None if r.score_total is None else Decimal(str(r.score_total)),
            score_factors=r.score_factors,
            rationale=r.rationale,
            selected=False,
        )
        for r in records
    ]
    session.add_all(rows)
    session.flush()
    return rows


def list_candidates(session: Session, business_id: uuid.UUID) -> list[DomainCandidate]:
    """Selected first, then ranked (valid) by rank, then invalid ones by name."""
    return list(
        session.scalars(
            select(DomainCandidate)
            .where(DomainCandidate.business_id == business_id)
            .order_by(
                DomainCandidate.selected.desc(),
                DomainCandidate.rank.asc().nulls_last(),
                DomainCandidate.name,
            )
        )
    )
