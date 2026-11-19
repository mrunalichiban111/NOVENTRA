"""Deterministic analysis glue: assumptions + severities -> finance -> scores -> scorecard.

Pure computation lives in app.finance; this module only loads/stores rows and assembles the
JSON the API serves. No LLM call happens here, so assumption edits are reproducible and free.
Every number traces to a source: assumptions carry source llm|user, the Monte Carlo carries
its persisted seed, scores come from app.finance.scoring.
"""

import uuid
from dataclasses import asdict
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.finance import config as fc
from app.finance.models import FinanceInputs, Range
from app.finance.montecarlo import new_seed, simulate
from app.finance.scenarios import run_scenarios
from app.finance.scoring import (
    RiskSeverity,
    display_score,
    economics_score,
    overall_prelaunch,
    risk_score,
)
from app.finance.verdict import decide_verdict
from app.models import Assumption, Business, Scorecard
from app.pipeline import status
from app.pipeline.status import Actor

STAGE = "pre"
Q = Decimal("0.0001")
NOTES = (
    "Probabilities and cash ranges are simulated from the assumptions, not observed outcomes.",
    "Assumptions, competitors and risk ratings are model-derived estimates and are unverified.",
)


class AssumptionEditError(ValueError):
    """Edited assumptions are invalid as a complete set."""


class WrongStage(AssumptionEditError):
    """Edits are only allowed once the analysis exists (GATED)."""


def load_assumptions(session: Session, business_id: uuid.UUID) -> list[Assumption]:
    rows = session.scalars(select(Assumption).where(Assumption.business_id == business_id))
    order = {k: i for i, k in enumerate(fc.ASSUMPTION_KEYS)}
    return sorted(rows, key=lambda a: order.get(a.key, 99))


def latest_scorecard(session: Session, business_id: uuid.UUID) -> Scorecard | None:
    return session.scalars(
        select(Scorecard)
        .where(Scorecard.business_id == business_id, Scorecard.stage == STAGE)
        .order_by(Scorecard.version.desc())
        .limit(1)
    ).first()


def _f(value: Decimal | float | None) -> float:
    return float(value) if value is not None else 0.0


def finance_inputs(
    values: dict[str, tuple[float, float, float]], starting_budget: Decimal | None
) -> tuple[FinanceInputs, str]:
    """Build validated inputs. A missing budget is assumed to equal the high startup cost."""
    ranges = {k: Range(*values[k]) for k in fc.ASSUMPTION_KEYS}
    if starting_budget is None:
        budget, source = ranges["startup_cost"].high, "assumed_equal_to_high_startup_cost"
    else:
        budget, source = float(starting_budget), "founder"
    return FinanceInputs(starting_budget=budget, **ranges), source


def _values(rows: list[Assumption]) -> dict[str, tuple[float, float, float]]:
    got = {a.key: (_f(a.low), _f(a.base), _f(a.high)) for a in rows}
    missing = [k for k in fc.ASSUMPTION_KEYS if k not in got]
    if missing:
        raise AssumptionEditError(f"missing assumptions: {missing}")
    return got


def compute_details(
    inputs: FinanceInputs,
    budget_source: str,
    severities: dict[str, dict[str, Any]],
    seed: int,
    rows: list[Assumption],
    pivots: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return (scorecard column values, details_json). Scores stay full precision for the verdict."""
    mc = simulate(inputs, seed)
    scenarios = run_scenarios(inputs)
    sev = {k: RiskSeverity(v["severity"], v["justification"]) for k, v in severities.items()}
    e, r = economics_score(mc.p_profitable), risk_score(sev)
    overall = overall_prelaunch(e, r)
    verdict = decide_verdict(overall, e=e, r=r)
    details = {
        "seed": seed,
        "starting_budget": inputs.starting_budget,
        "starting_budget_source": budget_source,
        "scenarios": {name: asdict(res) for name, res in scenarios.items()},
        "monte_carlo": {**asdict(mc), "simulated": True},
        "scores_full_precision": {"E": e, "R": r, "overall": overall},
        "risk_severities": severities,
        "explanation": list(verdict.explanation),
        "next_actions": [{"text": p, "source": "llm"} for p in pivots[:3]],
        "assumptions": [
            {
                "key": a.key,
                "low": _f(a.low),
                "base": _f(a.base),
                "high": _f(a.high),
                "source": a.source,
            }
            for a in rows
        ],
        "notes": list(NOTES),
    }
    columns = {
        "economics": Decimal(str(display_score(e))),
        "risk": Decimal(str(display_score(r))),
        "overall": Decimal(str(display_score(overall))),
        "verdict": verdict.verdict.value,
    }
    return columns, details


def write_scorecard(
    session: Session, business: Business, rows: list[Assumption], *, seed: int | None = None
) -> Scorecard:
    """Compute and store the next pre-launch scorecard version. Does not commit."""
    previous = latest_scorecard(session, business.id)
    if seed is None:
        seed = previous.details_json["seed"] if previous else new_seed()
    inputs, budget_source = finance_inputs(_values(rows), business.starting_budget)
    premortem = business.premortem_json or {}
    columns, details = compute_details(
        inputs,
        budget_source,
        premortem["risk_severities"],
        seed,
        rows,
        premortem.get("suggested_pivots", []),
    )
    version = (previous.version if previous else 0) + 1
    card = Scorecard(
        business_id=business.id,
        version=version,
        stage=STAGE,
        demand=None,
        details_json=details,
        **columns,
    )
    session.add(card)
    session.flush()
    return card


def apply_assumption_edit(
    session: Session,
    business_id: uuid.UUID,
    edits: list[dict[str, Any]],
    *,
    actor: Actor,
) -> tuple[Business, Scorecard | None, dict[str, dict[str, list[float]]]]:
    """Merge edits into the full set, validate it, recompute, version and audit. Does not commit.

    Raises status.BusinessNotFound, WrongStage (not GATED; API maps to 409) or
    AssumptionEditError (invalid resulting set; API maps to 422).
    """
    business = status.lock_business(session, business_id)
    if business.status != "GATED":
        raise WrongStage(f"Assumptions can only be edited at GATED (currently {business.status})")
    rows = load_assumptions(session, business_id)
    by_key = {a.key: a for a in rows}
    merged = _values(rows)
    changed: dict[str, dict[str, list[float]]] = {}
    for edit in edits:
        key = edit["key"]
        if key not in by_key:
            raise AssumptionEditError(f"unknown assumption key: {key}")
        old = merged[key]
        new = list(old)
        for i, field in enumerate(("low", "base", "high")):
            if edit.get(field) is not None:
                new[i] = float(Decimal(str(edit[field])).quantize(Q))
        if tuple(new) != old:
            changed[key] = {
                f: [old[i], new[i]]
                for i, f in enumerate(("low", "base", "high"))
                if new[i] != old[i]
            }
            merged[key] = (new[0], new[1], new[2])
    # Validate the COMPLETE resulting set before touching any row.
    try:
        finance_inputs(merged, business.starting_budget)
    except ValueError as exc:
        raise AssumptionEditError(str(exc)) from exc
    if not changed:
        return business, latest_scorecard(session, business_id), changed

    for key in changed:
        row = by_key[key]
        row.low, row.base, row.high = (Decimal(str(v)).quantize(Q) for v in merged[key])
        row.source = "user"
    session.flush()
    card = write_scorecard(session, business, load_assumptions(session, business_id))
    status.record_event(
        session,
        business,
        actor=actor,
        action="assumptions.edited",
        reason="Assumptions edited by user; scorecard recomputed without a model call",
        version_ref=f"pre-v{card.version}",
        details={"changed": changed, "scorecard_version": card.version, "verdict": card.verdict},
    )
    return business, card, changed


__all__ = ["apply_assumption_edit", "write_scorecard", "load_assumptions", "latest_scorecard"]
