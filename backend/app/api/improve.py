"""Improvement studio routes (M6: FR-IM-1..3, FR-IM-5, FR-IM-6, FR-GT-1). Thin wrappers over
app/pipeline/improve.py. Arena-derived numbers are SIMULATED and labelled so. Nothing goes live
without an explicit founder click AND the D >= D_original + 3 rule on comparable evaluations.
Errors use detail={"code", "message"}.
"""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.agents import personas as agent
from app.api.ratelimit import rate_limit
from app.database import get_session
from app.llm.base import BudgetExceeded, LLMError
from app.models import Business, ImprovementAttempt, PersonaEvaluation, SiteVersion
from app.pipeline import improve
from app.pipeline.status import BusinessNotFound

SessionDep = Annotated[Session, Depends(get_session)]
router = APIRouter(prefix="/api/businesses", tags=["improve"])


class AnswerIn(BaseModel):
    answer: str | None = Field(default=None, max_length=improve.ANSWER_MAX)
    not_decided: bool = False


class ApproveIn(BaseModel):
    item_ids: list[str] = Field(min_length=1, max_length=8)
    approve_consequential: bool = False


class RollbackIn(BaseModel):
    to_version: int = Field(ge=1)


class EvalSummary(BaseModel):
    evaluation_id: uuid.UUID | None
    site_version: int | None
    status: str | None
    d_score: float | None
    d_se: float | None
    completed_runs: int | None = None


class AttemptOut(BaseModel):
    simulated: bool = True
    label: str = improve.SIMULATED_LABEL
    business_id: uuid.UUID
    attempt_id: uuid.UUID | None = None
    status: str  # none | awaiting_answers | proposed | ... | promoted | not_promoted | ...
    reason: str | None = None
    opportunities: list[dict[str, Any]] = []
    questions: list[dict[str, Any]] = []
    answers: dict[str, Any] = {}
    proposal: dict[str, Any] = {}
    source_version: int | None = None
    draft_version: int | None = None
    original: EvalSummary | None = None
    draft: EvalSummary | None = None
    delta_demand: float | None = None
    threshold: float = float(improve.PROMOTION_THRESHOLD)
    promote_requested: bool = False
    note: str = (
        "D is simulated. A change of about 3 points can be within noise; compare the SE. "
        "Nothing changes on your live site until you approve and the rule is met."
    )


def _err(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _business(session: Session, business_id: uuid.UUID) -> Business:
    b = session.get(Business, business_id)
    if b is None:
        raise _err(404, "not_found", f"Business {business_id} not found")
    return b


def _summary(session: Session, eid: uuid.UUID | None) -> EvalSummary | None:
    if eid is None:
        return None
    ev = session.get(PersonaEvaluation, eid)
    if ev is None:
        return None
    sv = session.get(SiteVersion, ev.site_version_id)
    from sqlalchemy import func, select

    from app.models import PersonaRun

    done = session.scalar(select(func.count()).where(PersonaRun.evaluation_id == ev.id))
    complete = ev.status == "complete"
    return EvalSummary(
        evaluation_id=ev.id,
        site_version=sv.version if sv else None,
        status=ev.status,
        d_score=float(ev.d_score) if complete and ev.d_score is not None else None,
        d_se=float(ev.d_se) if complete and ev.d_se is not None else None,
        completed_runs=done,
    )


def _out(session: Session, business_id: uuid.UUID, a: ImprovementAttempt | None) -> AttemptOut:
    if a is None:
        return AttemptOut(business_id=business_id, status="none")
    pj = a.proposal_json or {}
    return AttemptOut(
        business_id=business_id,
        attempt_id=a.id,
        status=a.status,
        reason=a.reason,
        opportunities=a.clusters_json or [],
        questions=a.questions_json or [],
        answers=a.answers_json or {},
        proposal={k: v for k, v in pj.items() if k not in ("run_id",)},
        source_version=a.source_version,
        draft_version=a.to_version,
        original=_summary(session, a.original_evaluation_id),
        draft=_summary(session, a.draft_evaluation_id),
        delta_demand=float(a.delta_demand) if a.delta_demand is not None else None,
        promote_requested=bool(pj.get("promote_requested") or pj.get("promoting")),
    )


def _run(session: Session, business_id: uuid.UUID, fn):  # noqa: ANN001, ANN202
    _business(session, business_id)
    try:
        return fn()
    except BusinessNotFound:
        raise _err(404, "not_found", f"Business {business_id} not found") from None
    except improve.ImproveError as exc:
        raise _err(exc.http, exc.code, str(exc)) from None
    except BudgetExceeded as exc:
        raise _err(503, "budget_exceeded", str(exc)) from None
    except LLMError as exc:
        raise _err(503, "model_unavailable", f"The rewrite model did not answer: {exc}") from None


@router.get("/{business_id}/improve", response_model=AttemptOut)
def get_attempt(business_id: uuid.UUID, session: SessionDep) -> AttemptOut:
    _business(session, business_id)
    return _out(session, business_id, improve.latest_attempt(session, business_id))


@router.post("/{business_id}/improve/start", response_model=AttemptOut, status_code=201,
             dependencies=[Depends(rate_limit)])  # fmt: skip
def start(business_id: uuid.UUID, session: SessionDep, response: Response) -> AttemptOut:
    attempt, created = _run(session, business_id, lambda: improve.start(session, business_id))
    if not created:
        response.status_code = 200
    return _out(session, business_id, attempt)


@router.patch("/{business_id}/improve/questions/{qid}", response_model=AttemptOut,
              dependencies=[Depends(rate_limit)])  # fmt: skip
def answer(business_id: uuid.UUID, qid: str, body: AnswerIn, session: SessionDep) -> AttemptOut:
    a = _run(session, business_id,
             lambda: improve.answer(session, business_id, qid, body.answer, body.not_decided))  # fmt: skip
    return _out(session, business_id, a)


@router.post("/{business_id}/improve/proposal", response_model=AttemptOut,
             dependencies=[Depends(rate_limit)])  # fmt: skip
def proposal(business_id: uuid.UUID, session: SessionDep) -> AttemptOut:
    a = _run(session, business_id, lambda: improve.generate_proposal(session, business_id))
    return _out(session, business_id, a)


@router.post("/{business_id}/improve/approve", response_model=AttemptOut, status_code=202,
             dependencies=[Depends(rate_limit)])  # fmt: skip
def approve(business_id: uuid.UUID, body: ApproveIn, session: SessionDep) -> AttemptOut:
    try:
        agent.assert_haiku()  # the draft will be evaluated by Claude Haiku only
    except LLMError as exc:
        raise _err(503, "evaluator_misconfigured", str(exc)) from None
    a = _run(session, business_id, lambda: improve.approve(
        session, business_id, body.item_ids, body.approve_consequential))  # fmt: skip
    return _out(session, business_id, a)


@router.post("/{business_id}/improve/reject", response_model=AttemptOut,
             dependencies=[Depends(rate_limit)])  # fmt: skip
def reject(business_id: uuid.UUID, session: SessionDep) -> AttemptOut:
    return _out(session, business_id,
                _run(session, business_id, lambda: improve.reject(session, business_id)))  # fmt: skip


@router.post("/{business_id}/improve/discard", response_model=AttemptOut,
             dependencies=[Depends(rate_limit)])  # fmt: skip
def discard(business_id: uuid.UUID, session: SessionDep) -> AttemptOut:
    return _out(session, business_id,
                _run(session, business_id, lambda: improve.discard(session, business_id)))  # fmt: skip


@router.post("/{business_id}/improve/promote", response_model=AttemptOut, status_code=202,
             dependencies=[Depends(rate_limit)])  # fmt: skip
def promote(business_id: uuid.UUID, session: SessionDep) -> AttemptOut:
    a = _run(session, business_id, lambda: improve.request_promotion(session, business_id))
    return _out(session, business_id, a)


@router.post("/{business_id}/site/rollback", status_code=202,
             dependencies=[Depends(rate_limit)])  # fmt: skip
def rollback(business_id: uuid.UUID, body: RollbackIn, session: SessionDep) -> dict[str, Any]:
    appr = _run(session, business_id,
                lambda: improve.request_rollback(session, business_id, body.to_version))  # fmt: skip
    return {"business_id": str(business_id), "to_version": body.to_version,
            "request_id": str(appr.id), "status": "queued"}  # fmt: skip
