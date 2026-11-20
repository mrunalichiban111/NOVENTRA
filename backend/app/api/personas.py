"""Persona arena routes (M5: FR-AR-1..4, FR-AR-7, FR-AR-8). Thin wrappers over app/pipeline/arena.

Every response is labelled SIMULATED: these are model-simulated customer reactions, never real
market research. The worker does all model calls; these routes only queue work and read results
(polling, no streaming). Errors use detail={"code", "message"}.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import config
from app.agents import personas as agent
from app.api.ratelimit import rate_limit
from app.database import get_session
from app.llm.base import ConfigError
from app.llm.structured import is_mock
from app.llm.usage import spend_last_24h_usd
from app.models import Business, PersonaPanel, PersonaRun
from app.pipeline import arena
from app.pipeline.status import BusinessNotFound

SessionDep = Annotated[Session, Depends(get_session)]
router = APIRouter(prefix="/api/businesses", tags=["personas"])

NOISE_NOTE = (
    "Simulated scores vary between runs. A change of about 3 points can be within noise; "
    "compare the standard error (SE) before drawing conclusions."
)


class _Simulated(BaseModel):
    simulated: bool = True
    label: str = arena.SIMULATED_LABEL


class PanelOut(_Simulated):
    business_id: uuid.UUID
    panel_id: uuid.UUID | None
    status: str  # none | pending | generating | ready | failed
    error: str | None = None
    count: int = 0
    personas: list[dict[str, Any]] | None = None
    changed: bool | None = None


class EvaluateRequest(BaseModel):
    site_version: int | None = Field(default=None, ge=1)


class EvaluationOut(_Simulated):
    business_id: uuid.UUID
    evaluation_id: uuid.UUID
    site_version: int
    status: str
    changed: bool


class ResultsOut(_Simulated):
    business_id: uuid.UUID
    evaluation_id: uuid.UUID | None = None
    site_version: int | None = None
    status: str  # not_started | queued | running | complete | incomplete
    completed_runs: int = 0
    persona_count: int = 0  # actual simulated personas in the panel
    expected_runs: int = 0  # actual personas x repeats, set from the evaluation
    d_score: float | None = None
    d_se: float | None = None
    comparable_key: dict[str, Any] | None = None
    personas: list[dict[str, Any]] = []
    objections: list[dict[str, Any]] = []
    friction_by_section: dict[str, int] = {}
    positive_signals: dict[str, int] = {}
    missing_info: list[dict[str, Any]] = []
    converted: int = 0
    error_code: str | None = None
    error: str | None = None
    retryable: bool = False
    note: str = NOISE_NOTE
    updated_at: datetime | None = None


def _err(status_code: int, code: str, message: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail={"code": code, "message": message})


def _business(session: Session, business_id: uuid.UUID) -> Business:
    business = session.get(Business, business_id)
    if business is None:
        raise _err(404, "not_found", f"Business {business_id} not found")
    return business


def _arena_error(exc: arena.ArenaError) -> HTTPException:
    status_code = 422 if isinstance(exc, arena.InvalidVersion) else 409
    return _err(status_code, exc.code, str(exc))


def _daily_precheck() -> None:
    """Friendly early refusal; the real, concurrency-safe enforcement is in LLMGuard.before."""
    if is_mock():
        return
    if spend_last_24h_usd() >= config.DAILY_SPEND_CAP_USD:
        raise _err(
            503, "budget_exceeded", "Today's AI budget is used up. Please try again tomorrow."
        )


def _panel_out(
    business_id: uuid.UUID, panel: PersonaPanel | None, changed: bool | None
) -> PanelOut:
    if panel is None:
        return PanelOut(business_id=business_id, panel_id=None, status="none", changed=changed)
    personas = panel.personas_json if panel.status == "ready" else None
    return PanelOut(
        business_id=business_id,
        panel_id=panel.id,
        status=panel.status,
        error=panel.error,
        count=len(personas or []),
        personas=personas,
        changed=changed,
    )


@router.post(
    "/{business_id}/personas/generate",
    response_model=PanelOut,
    status_code=202,
    dependencies=[Depends(rate_limit)],
)
def generate_personas(business_id: uuid.UUID, session: SessionDep, response: Response) -> PanelOut:
    """Queue the one simulated 30-persona panel for this business (idempotent)."""
    _business(session, business_id)
    _daily_precheck()
    try:
        req = arena.request_panel(session, business_id)
    except BusinessNotFound:
        raise _err(404, "not_found", f"Business {business_id} not found") from None
    except arena.ArenaError as exc:
        raise _arena_error(exc) from None
    if not req.changed:
        response.status_code = 200
    return _panel_out(business_id, req.row, req.changed)


@router.get("/{business_id}/personas", response_model=PanelOut)
def get_personas(business_id: uuid.UUID, session: SessionDep) -> PanelOut:
    _business(session, business_id)
    return _panel_out(business_id, arena.get_panel(session, business_id), None)


@router.post(
    "/{business_id}/personas/evaluate",
    response_model=EvaluationOut,
    status_code=202,
    dependencies=[Depends(rate_limit)],
)
def evaluate_personas(
    business_id: uuid.UUID,
    session: SessionDep,
    response: Response,
    body: EvaluateRequest | None = None,
) -> EvaluationOut:
    """Queue a simulated evaluation of a site version (default: the live one). Repeats are
    idempotent; an incomplete evaluation is re-queued and only runs what is missing."""
    _business(session, business_id)
    try:
        agent.assert_haiku()  # Claude Haiku only; refuse before queueing anything
    except ConfigError as exc:
        raise _err(503, "evaluator_misconfigured", str(exc)) from None
    _daily_precheck()
    version = body.site_version if body else None
    try:
        req = arena.request_evaluation(session, business_id, version)
    except BusinessNotFound:
        raise _err(404, "not_found", f"Business {business_id} not found") from None
    except arena.ArenaError as exc:
        raise _arena_error(exc) from None
    if not req.changed:
        response.status_code = 200
    ev = req.row
    found = arena.get_evaluation(session, business_id, version)
    sv_version = found[1].version if found else (version or 0)
    return EvaluationOut(
        business_id=business_id,
        evaluation_id=ev.id,
        site_version=sv_version,
        status=ev.status,
        changed=req.changed,
    )


@router.get("/{business_id}/personas/results", response_model=ResultsOut)
def get_results(
    business_id: uuid.UUID, session: SessionDep, site_version: int | None = None
) -> ResultsOut:
    """Live, deterministic view of the raw runs (cards fill in while running). D and SE are
    shown only for a complete evaluation; an incomplete one has no D (never extrapolated)."""
    _business(session, business_id)
    found = arena.get_evaluation(session, business_id, site_version)
    if found is None:
        return ResultsOut(business_id=business_id, status="not_started")
    ev, sv = found
    panel = session.get(PersonaPanel, ev.panel_id)
    runs = list(session.scalars(select(PersonaRun).where(PersonaRun.evaluation_id == ev.id)))
    agg = arena.aggregate(runs, list(panel.personas_json or []), arena.REPEATS)
    complete = ev.status == "complete"
    return ResultsOut(
        business_id=business_id,
        evaluation_id=ev.id,
        site_version=sv.version,
        status=ev.status,
        completed_runs=len(runs),
        persona_count=len(panel.personas_json or []),
        expected_runs=ev.expected_runs,
        d_score=float(ev.d_score) if complete and ev.d_score is not None else None,
        d_se=float(ev.d_se) if complete and ev.d_se is not None else None,
        comparable_key={
            "panel_id": str(ev.panel_id),
            "evaluator_model": ev.evaluator_model,
            "prompt_version": ev.prompt_version,
            "rubric_version": ev.rubric_version,
        },
        personas=agg["personas"],
        objections=agg["objections"],
        friction_by_section=agg["friction_by_section"],
        positive_signals=agg["positive_signals"],
        missing_info=agg["missing_info"],
        converted=agg["converted"],
        error_code=ev.error_code,
        error=ev.error,
        retryable=ev.status == "incomplete"
        and ev.error_code not in ("evaluator_misconfigured", "release_unavailable"),
        updated_at=ev.updated_at,
    )
