"""Business-name routes (M4A: FR-BR-1..5). Thin wrappers over app/names/service.py.

The unguessable business UUID is the capability (no login), as for the other business routes.
Errors use detail={"code", "message"} so clients can branch on a stable code.
"""

import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.orm import Session

from app import config
from app.api.ratelimit import rate_limit
from app.api.schemas import (
    AvailabilityOut,
    DomainCheckOut,
    GenerateNamesRequest,
    NameCandidateOut,
    NameSelected,
    NamesGenerated,
    NamesList,
)
from app.database import get_session
from app.domains import availability as av
from app.models import DomainCandidate
from app.names import service

SessionDep = Annotated[Session, Depends(get_session)]
router = APIRouter(prefix="/api/businesses", tags=["names"])
_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,100}$")


def _error(exc: service.NamesError) -> HTTPException:
    return HTTPException(
        status_code=exc.http_status, detail={"code": exc.code, "message": str(exc)}
    )


def _check_out(row) -> DomainCheckOut:  # noqa: ANN001
    return DomainCheckOut(
        domain=f"{row.candidate.slug}{row.tld}",
        tld=row.tld,
        status=row.status,
        reason=row.reason,
        message=av.display_message(row.status, row.reason),
        raw_price=row.raw_price,
        currency=row.currency,
        definitive=row.definitive,
        checked_at=row.checked_at,
    )


def _candidate_out(c: DomainCandidate, taken: set[str]) -> NameCandidateOut:
    available = bool(c.valid and c.slug and (c.selected or c.slug not in taken))
    return NameCandidateOut(
        id=c.id,
        name=c.name,
        slug=c.slug,
        rank=c.rank,
        score_total=c.score_total,
        score_factors=c.score_factors or {},
        valid=c.valid,
        invalid_reason=c.invalid_reason,
        rationale=c.rationale,
        subdomain_available=available,
        subdomain_host=service.subdomain_host(c.slug) if c.valid else None,
        selected=c.selected,
        domain_checks=[_check_out(k) for k in c.checks],
    )


def _list(session: Session, business_id: uuid.UUID) -> NamesList:
    business, rows, taken = service.list_names(session, business_id)
    return NamesList(
        business_id=business.id,
        status=business.status,
        subdomains_configured=bool(config.BASE_DOMAIN),
        generations_used=service.generation_count(session, business_id),
        generations_max=service.MAX_GENERATIONS,
        candidates=[_candidate_out(c, taken) for c in rows],
    )


@router.post(
    "/{business_id}/names/generate",
    response_model=NamesGenerated,
    dependencies=[Depends(rate_limit)],
)
def generate_names(
    business_id: uuid.UUID,
    session: SessionDep,
    payload: GenerateNamesRequest | None = None,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> NamesGenerated:
    if idempotency_key is not None and not _KEY.fullmatch(idempotency_key):
        raise HTTPException(
            status_code=422,
            detail={"code": "bad_idempotency_key", "message": "Use 1-100 of [A-Za-z0-9._:-]."},
        )
    body = payload.model_dump() if payload else {}
    try:
        outcome = service.generate(session, business_id, idempotency_key=idempotency_key, body=body)
    except service.NamesError as exc:
        session.rollback()
        raise _error(exc) from None
    listing = _list(session, business_id)
    return NamesGenerated(**listing.model_dump(), run_id=outcome.run_id, replayed=outcome.replayed)


@router.get("/{business_id}/names", response_model=NamesList)
def list_names(business_id: uuid.UUID, session: SessionDep) -> NamesList:
    try:
        return _list(session, business_id)
    except service.NamesError as exc:
        raise _error(exc) from None


@router.post(
    "/{business_id}/names/{candidate_id}/availability",
    response_model=AvailabilityOut,
    dependencies=[Depends(rate_limit)],
)
def check_availability(
    business_id: uuid.UUID, candidate_id: uuid.UUID, session: SessionDep
) -> AvailabilityOut:
    try:
        cand, results = service.check_availability(session, business_id, candidate_id)
    except service.NamesError as exc:
        session.rollback()
        raise _error(exc) from None
    stored = {k.tld: k for k in cand.checks}
    return AvailabilityOut(
        candidate_id=cand.id,
        slug=cand.slug or "",
        results=[
            DomainCheckOut(
                domain=r.domain,
                tld=r.tld,
                status=r.status.value,
                reason=r.reason,
                message=av.display_message(r.status, r.reason),
                raw_price=r.raw_price,
                currency=r.currency,
                definitive=r.definitive,
                checked_at=stored[r.tld].checked_at if r.tld in stored else None,
                cached=r.cached,
            )
            for r in results
        ],
    )


@router.post(
    "/{business_id}/names/{candidate_id}/select",
    response_model=NameSelected,
    dependencies=[Depends(rate_limit)],
)
def select_name(
    business_id: uuid.UUID,
    candidate_id: uuid.UUID,
    session: SessionDep,
    replace: bool = False,
) -> NameSelected:
    try:
        outcome = service.select_candidate(session, business_id, candidate_id, replace=replace)
    except service.NamesError as exc:
        session.rollback()
        raise _error(exc) from None
    business, _rows, taken = service.list_names(session, business_id)
    return NameSelected(
        business_status=business.status,
        subdomain_host=business.domain,
        changed=outcome.changed,
        replaced_slug=outcome.replaced_slug,
        candidate=_candidate_out(outcome.candidate, taken),
    )
