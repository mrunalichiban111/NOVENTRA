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
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from app.agents import moderation
from app.agents.premortem import RunLLM, run_assumption_extraction, run_premortem
from app.llm.base import RefusalError
from app.llm.structured import run_structured
from app.models import Assumption, Business, BusinessStatus
from app.pipeline import analysis
from app.pipeline.checkpoint import checkpoint, hold_lease, model_call_lease_s
from app.pipeline.runner import Handler, StageResult, current_run_id

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
    """PREMORTEM checkpoint (see app/pipeline/checkpoint.py)."""
    checkpoint(session, business.id, S.PREMORTEM, apply)


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
            # Hold the lease for the whole call first (the runner's renewal is only the TTL).
            hold_lease(session, business.id, S.PREMORTEM, model_call_lease_s("premortem"))
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
            hold_lease(
                session, business.id, S.PREMORTEM, model_call_lease_s("assumption_extraction")
            )
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


def make_deploy_stage(
    run_llm: RunLLM = run_structured,
    *,
    provider_factory: Callable[[], Any] | None = None,
    links: Any = None,
    require_public: bool | None = None,
) -> Handler:
    """DEPLOYING -> LIVE (M4B). The provider and settings are resolved per run, so a config
    error fails the stage visibly instead of crashing the worker."""

    def deploy_stage(session: Session, business: Business) -> StageResult:
        from app.deploy import service

        ctx = service.make_context(
            run_llm,
            provider_factory=provider_factory,
            links=links,
            require_public=require_public,
        )
        return service.run_deploy_stage(session, business, ctx)

    return deploy_stage


def pipeline_stages(
    run_llm: RunLLM = run_structured, **deploy_kw: Any
) -> dict[BusinessStatus, Handler]:
    """All implemented stages: M3 plus DEPLOYING (M4B). DOMAIN_PENDING and LIVE have no handler
    on purpose: DOMAIN_PENDING waits for an explicit deploy request, LIVE waits for M5."""
    return {**m3_stages(run_llm), S.DEPLOYING: make_deploy_stage(run_llm, **deploy_kw)}
