"""M3 stage handlers: INTAKE -> PREMORTEM (moderation, pre-mortem, assumptions, finance,
scorecard) -> GATED. GATED has no handler on purpose: the pipeline stops there until M4.

Resume model: the runner commits one transaction per stage, but the expensive model outputs
are committed as soon as they exist (pre-mortem, then assumptions), and each step is skipped
when its result is already stored. A retry after a failure therefore never repeats a model call
that already succeeded, and a finished stage is never re-run (the runner's expected_from guard).
Every real model call receives the runner's current run_id so the per-run cap applies.
"""

import logging
from collections.abc import Callable
from datetime import timedelta
from decimal import Decimal

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.agents import moderation
from app.agents.premortem import RunLLM, run_assumption_extraction, run_premortem
from app.config import WORKER_CLAIM_TTL_SECONDS
from app.llm.base import RefusalError
from app.llm.structured import run_structured
from app.models import Assumption, Business, BusinessStatus
from app.pipeline import analysis, status
from app.pipeline.runner import Handler, StageResult, current_run_id, current_worker_id

S = BusinessStatus
logger = logging.getLogger(__name__)
Q = Decimal("0.0001")


def _intake(_session: Session, _business: Business) -> StageResult:
    return StageResult(S.PREMORTEM, "Idea accepted for analysis")


def _reject(session: Session, business: Business, reason: str, action: str) -> StageResult:
    business.rejection_reason = reason
    session.flush()  # transition() re-reads the row (populate_existing); persist first
    logger.warning("business_id=%s rejected: %s", business.id, reason)
    return StageResult(S.REJECTED, reason, action=action)


def _checkpoint(session: Session, business: Business, apply: Callable[[Business], None]) -> None:
    """Commit a mid-stage result only if we still own the business.

    Takes the row lock (so no other worker can claim it meanwhile), then requires the status to
    still be PREMORTEM and, when run by a worker, a live lease (LeaseLost / StaleState end the
    stage quietly in the runner, writing nothing). A successful checkpoint extends the lease
    for the next long model call.
    """
    locked = status.lock_business(session, business.id)
    if locked.status != S.PREMORTEM:
        raise status.StaleState(f"Business {locked.id} is {locked.status}, expected PREMORTEM")
    worker = current_worker_id.get()
    if worker is not None:
        status.assert_lease(session, locked, worker)
        locked.locked_until = func.now() + timedelta(seconds=WORKER_CLAIM_TTL_SECONDS)
    apply(locked)
    session.commit()


def make_premortem_stage(run_llm: RunLLM = run_structured) -> Handler:
    def premortem_stage(session: Session, business: Business) -> StageResult:
        run_id = current_run_id.get()

        # 1. Safety moderation (FR-PR-3). Deterministic; no model call, no spend.
        if business.premortem_json is None:
            verdict = moderation.check_idea(business.idea_text)
            if not verdict.allowed:
                return _reject(
                    session,
                    business,
                    verdict.reason or "Rejected for safety",
                    "moderation.rejected",
                )

            # 2. Pre-mortem. A model refusal is a safety refusal, not a commercial verdict.
            try:
                stored = run_premortem(business, run_id=run_id, run_llm=run_llm)
            except RefusalError:
                return _reject(
                    session,
                    business,
                    "Rejected for safety: the model declined to analyse this idea. This is a "
                    "safety refusal, not a commercial verdict.",
                    "moderation.rejected",
                )

            def store_premortem(b: Business) -> None:
                if b.premortem_json is None:  # never overwrite an earlier result
                    b.premortem_json = stored
                    b.target_customer = stored["target_customer"][:2000]

            _checkpoint(session, business, store_premortem)  # keep the paid result

        # 3. Assumption extraction (source=llm).
        if not analysis.load_assumptions(session, business.id):
            extracted = run_assumption_extraction(business, run_id=run_id, run_llm=run_llm)

            def store_assumptions(b: Business) -> None:
                if analysis.load_assumptions(session, b.id):
                    return  # already stored by an earlier attempt
                for item in extracted.assumptions:
                    session.add(
                        Assumption(
                            business_id=b.id,
                            key=item.key,
                            low=Decimal(str(item.low)).quantize(Q),
                            base=Decimal(str(item.base)).quantize(Q),
                            high=Decimal(str(item.high)).quantize(Q),
                            rationale=item.rationale,
                            source="llm",
                        )
                    )

            _checkpoint(session, business, store_assumptions)

        # 4. Deterministic finance + scoring + verdict, stored as scorecard v1 (stage "pre").
        rows = analysis.load_assumptions(session, business.id)
        card = analysis.latest_scorecard(session, business.id) or analysis.write_scorecard(
            session, business, rows
        )
        first = card.details_json["explanation"][0] if card.details_json["explanation"] else ""
        return StageResult(
            S.GATED,
            f"Pre-launch verdict: {card.verdict}. E {card.economics}, R {card.risk}, "
            f"overall {card.overall}. {first}",
            version_ref=f"pre-v{card.version}",
        )

    return premortem_stage


def m3_stages(run_llm: RunLLM = run_structured) -> dict[BusinessStatus, Handler]:
    """Handlers for M3. GATED (and everything after) is intentionally absent."""
    return {S.INTAKE: _intake, S.PREMORTEM: make_premortem_stage(run_llm)}
