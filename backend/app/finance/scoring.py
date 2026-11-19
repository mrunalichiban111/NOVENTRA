"""Scores E, R and pre-launch overall (SRS Section 6). The LLM supplies severities only.

Every score function returns the FULL-PRECISION value, and the verdict is decided on that
value. Round only for display or storage with `display_score`; a score of 64.996 is shown
as 65.0 but is still below the Go threshold.
"""

import math
from dataclasses import dataclass

from app.finance import config


@dataclass(frozen=True)
class RiskSeverity:
    severity: int
    justification: str

    def __post_init__(self) -> None:
        s = self.severity
        if isinstance(s, bool) or not isinstance(s, int):
            raise ValueError(f"severity must be an integer, got {s!r}")
        if not config.SEVERITY_MIN <= s <= config.SEVERITY_MAX:
            raise ValueError(f"severity must be {config.SEVERITY_MIN}-{config.SEVERITY_MAX}")
        if not isinstance(self.justification, str) or not self.justification.strip():
            raise ValueError("severity needs a justification")


def _clamp(value: float) -> float:
    """Remove float noise only (e.g. -1e-14 or 100.00000000000001); never hides real values."""
    return min(100.0, max(0.0, value))


def _check_score(name: str, value: float) -> None:
    if not math.isfinite(value) or not 0 <= value <= 100:
        raise ValueError(f"{name} must be within 0-100, got {value!r}")


def economics_score(p_profitable: float) -> float:
    """E = 100 x P(profitable by month 6)."""
    if not math.isfinite(p_profitable) or not 0 <= p_profitable <= 1:
        raise ValueError(f"probability must be within 0-1, got {p_profitable!r}")
    return 100 * p_profitable


def risk_score(severities: dict[str, RiskSeverity]) -> float:
    """R = 100 x (1 - (weighted mean severity - 1) / 4)."""
    if set(severities) != set(config.RISK_WEIGHTS):
        raise ValueError(f"severities must be exactly {sorted(config.RISK_WEIGHTS)}")
    mean = sum(config.RISK_WEIGHTS[k] * severities[k].severity for k in config.RISK_WEIGHTS)
    span = config.SEVERITY_MAX - config.SEVERITY_MIN
    return _clamp(100 * (1 - (mean - config.SEVERITY_MIN) / span))


def overall_prelaunch(e: float, r: float) -> float:
    """Pre-launch overall = 0.58 E + 0.42 R."""
    _check_score("E", e)
    _check_score("R", r)
    w = config.PRELAUNCH_WEIGHTS
    return _clamp(w["E"] * e + w["R"] * r)


def display_score(value: float) -> float:
    """Rounded value for display and serialization only. Never feed this into a verdict."""
    return round(value, config.SCORE_DECIMALS)
