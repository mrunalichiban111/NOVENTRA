"""Verdict gate (SRS Section 6, FR-SC-3). Precedence: Rethink, then Go, else Go with changes."""

import math
from dataclasses import dataclass
from enum import StrEnum

from app.finance import config


class Verdict(StrEnum):
    GO = "go"
    GO_WITH_CHANGES = "go_with_changes"
    RETHINK = "rethink"


@dataclass(frozen=True)
class VerdictResult:
    verdict: Verdict
    explanation: tuple[str, ...]


def _fmt(x: float) -> str:
    """Display only: up to 4 decimals, trailing zeros trimmed. Comparisons never use this."""
    return f"{x:.4f}".rstrip("0").rstrip(".")


def decide_verdict(overall: float, *, e: float, r: float) -> VerdictResult:
    """Pre-launch verdict (M3). Inputs must be full-precision scores, not rounded ones.

    Post-deploy scoring (D and the 0.40/0.35/0.25 overall) is deliberately not part of M3.
    """
    components = {"Economics (E)": e, "Risk (R)": r}
    for name, value in {"overall": overall, **components}.items():
        if not math.isfinite(value) or not 0 <= value <= 100:
            raise ValueError(f"{name} must be within 0-100, got {value!r}")

    lines: list[str] = [
        "Scores: "
        + ", ".join(f"{k} {_fmt(v)}" for k, v in components.items())
        + f", overall {_fmt(overall)}."
    ]
    weak = {k: v for k, v in components.items() if v < config.COMPONENT_FLOOR}

    rethink_reasons: list[str] = []
    if overall < config.RETHINK_OVERALL_BELOW:
        rethink_reasons.append(
            f"Overall {_fmt(overall)} is below the Rethink line of {_fmt(config.RETHINK_OVERALL_BELOW)}."
        )
    if e < config.RETHINK_E_BELOW:
        rethink_reasons.append(
            f"Economics (E) {_fmt(e)} is below {_fmt(config.RETHINK_E_BELOW)}: "
            "the business is very unlikely to be profitable by month 6."
        )
    if rethink_reasons:
        return VerdictResult(Verdict.RETHINK, tuple([*rethink_reasons, *lines]))

    if overall >= config.GO_OVERALL_MIN and not weak:
        lines.insert(
            0,
            f"Overall {_fmt(overall)} is at least {_fmt(config.GO_OVERALL_MIN)} and every "
            f"component is at least {_fmt(config.COMPONENT_FLOOR)}.",
        )
        return VerdictResult(Verdict.GO, tuple(lines))

    reasons: list[str] = []
    if overall < config.GO_OVERALL_MIN:
        reasons.append(
            f"Overall {_fmt(overall)} is between {_fmt(config.RETHINK_OVERALL_BELOW)} and "
            f"{_fmt(config.GO_OVERALL_MIN)}."
        )
    for name, value in weak.items():
        reasons.append(
            f"{name} {_fmt(value)} is below the component floor of {_fmt(config.COMPONENT_FLOOR)}."
        )
    return VerdictResult(Verdict.GO_WITH_CHANGES, tuple([*reasons, *lines]))
