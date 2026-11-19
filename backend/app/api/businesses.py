import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.ratelimit import rate_limit
from app.api.schemas import (
    AssumptionOut,
    AssumptionsPatch,
    AuditEntry,
    BusinessCreate,
    BusinessCreated,
    BusinessDetail,
    FinanceOut,
    PipelineInfo,
    ScorecardOut,
)
from app.config import MAX_STAGE_ATTEMPTS
from app.database import get_session
from app.models import Business, BusinessStatus
from app.pipeline import analysis, repository, status
from app.pipeline.status import PAUSE_STAGES, PIPELINE_ORDER, TERMINAL, Actor

SessionDep = Annotated[Session, Depends(get_session)]
router = APIRouter(prefix="/api/businesses", tags=["businesses"])


def _next_stage(current: BusinessStatus) -> BusinessStatus | None:
    try:
        position = PIPELINE_ORDER.index(current)
    except ValueError:  # REJECTED / FAILED are off the happy path
        return None
    return PIPELINE_ORDER[position + 1] if position + 1 < len(PIPELINE_ORDER) else None


def _detail(session: Session, business: Business) -> BusinessDetail:
    current = BusinessStatus(business.status)
    next_stage = None if current in PAUSE_STAGES else _next_stage(current)
    failed = current == BusinessStatus.FAILED
    pipeline = PipelineInfo(
        current_stage=current,
        next_stage=next_stage,
        terminal=current in TERMINAL,
        in_progress=business.locked_by is not None and current not in TERMINAL and not failed,
        attempts=business.attempts,
        max_attempts=MAX_STAGE_ATTEMPTS,
        failed_stage=business.failed_stage,
        last_error=business.last_error,
        retryable=failed and business.attempts < MAX_STAGE_ATTEMPTS,
        history=[AuditEntry.model_validate(e) for e in repository.list_audit(session, business.id)],
    )
    card = analysis.latest_scorecard(session, business.id)
    scorecard = finance = None
    if card is not None:
        d = card.details_json
        scorecard = ScorecardOut(
            version=card.version,
            stage=card.stage,
            economics=card.economics,
            risk=card.risk,
            overall=card.overall,
            verdict=card.verdict,
            explanation=d["explanation"],
            next_actions=d["next_actions"],
            risk_severities=d["risk_severities"],
            notes=d["notes"],
        )
        finance = FinanceOut(
            starting_budget=d["starting_budget"],
            starting_budget_source=d["starting_budget_source"],
            scenarios=d["scenarios"],
            monte_carlo=d["monte_carlo"],
            seed=d["seed"],
        )
    return BusinessDetail(
        id=business.id,
        idea_text=business.idea_text,
        market=business.market,
        price_guess=business.price_guess,
        starting_budget=business.starting_budget,
        target_customer=business.target_customer,
        status=business.status,
        domain=business.domain,
        created_at=business.created_at,
        updated_at=business.updated_at,
        pipeline=pipeline,
        rejection_reason=business.rejection_reason,
        premortem=business.premortem_json,
        assumptions=[
            AssumptionOut(
                key=a.key,
                low=a.low,
                base=a.base,
                high=a.high,
                rationale=a.rationale,
                source=a.source,
            )
            for a in analysis.load_assumptions(session, business.id)
        ],
        finance=finance,
        scorecard=scorecard,
        verdict=card.verdict if card else None,
        verdict_explanation=card.details_json["explanation"] if card else [],
    )


def _get_or_404(session: Session, business_id: uuid.UUID) -> Business:
    business = repository.get_business(session, business_id)
    if business is None:
        raise HTTPException(status_code=404, detail=f"Business {business_id} not found")
    return business


@router.post(
    "", response_model=BusinessCreated, status_code=201, dependencies=[Depends(rate_limit)]
)
def create_business(payload: BusinessCreate, session: SessionDep) -> BusinessCreated:
    business = repository.create_business(
        session,
        idea_text=payload.idea_text,
        market=payload.market,
        price_guess=payload.price_guess,
        starting_budget=payload.starting_budget,
        actor=Actor.user(),
    )
    session.commit()
    return BusinessCreated(id=business.id, status=business.status)


@router.get("/{business_id}", response_model=BusinessDetail)
def get_business(business_id: uuid.UUID, session: SessionDep) -> BusinessDetail:
    return _detail(session, _get_or_404(session, business_id))


@router.post(
    "/{business_id}/retry", response_model=BusinessDetail, dependencies=[Depends(rate_limit)]
)
def retry_business(business_id: uuid.UUID, session: SessionDep) -> BusinessDetail:
    try:
        business = status.retry(
            session, business_id, actor=Actor.user(), reason="Manual retry via API"
        )
        session.commit()
    except status.BusinessNotFound:
        session.rollback()
        raise HTTPException(status_code=404, detail=f"Business {business_id} not found") from None
    except status.RetryNotAllowed as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except status.RetryLimitExceeded as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    return _detail(session, business)


@router.patch("/{business_id}/assumptions", response_model=BusinessDetail)
def patch_assumptions(
    business_id: uuid.UUID, payload: AssumptionsPatch, session: SessionDep
) -> BusinessDetail:
    """Edit assumptions (FR-FIN-2). Recomputes finance and the scorecard; never calls a model."""
    edits = [a.model_dump(exclude_none=False) for a in payload.assumptions]
    try:
        business, _card, _changed = analysis.apply_assumption_edit(
            session, business_id, edits, actor=Actor.user()
        )
        session.commit()
    except status.BusinessNotFound:
        session.rollback()
        raise HTTPException(status_code=404, detail=f"Business {business_id} not found") from None
    except analysis.WrongStage as exc:
        session.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from None
    except analysis.AssumptionEditError as exc:
        session.rollback()
        raise HTTPException(status_code=422, detail=str(exc)) from None
    return _detail(session, business)
