"""Every finance and scoring constant lives here (SRS Section 6, FR-FIN-3..5).

Nothing in app/finance outside this module may hard-code a weight, threshold, horizon,
simulation count or endpoint direction. Pure Python: no database, no LLM.

Conventions (documented once, relied on everywhere)
---------------------------------------------------
* Money is in the idea's currency units (INR by default); the engine is unit-agnostic.
* Conversion is a FRACTION in [0, 1] (0.02 means 2 %), never a percentage.
* Flat monthly model: every month has the same orders, profit and cash flow. There is no
  ramp-up, churn, seasonality, tax or working capital. Month 0 is "today, after paying the
  startup cost"; month m is m full months later.
* Cumulative cash(m) = starting_budget - startup_cost + m * monthly_net_cash_flow.
  Monthly net cash flow equals monthly operating profit (no financing or capex flows).
* Six-month profitability (the Monte Carlo metric and score E) uses cumulative cash at
  month PROFIT_MONTH, strictly greater than zero. It is separate from break-even month and
  runway, which are projected over PROJECTION_HORIZON_MONTHS.
* Break-even month = first month m in 1..horizon whose cumulative OPERATING cash flow
  (m * monthly operating profit, starting budget ignored) is >= startup cost; else None.
* Runway = number of full months cumulative cash stays >= 0 while burning, i.e.
  floor(opening cash / monthly burn). Opening cash below zero means runway 0.
* Scenario endpoints: for a "higher is better" input the adverse endpoint is `low` and the
  favorable endpoint is `high`; for a cost input it is the reverse. See ENDPOINT_DIRECTION.
* Runway status meanings (opening cash = starting budget - startup cost):
    underfunded     opening cash < 0 (startup cost exceeds the budget). Runway is 0 months,
                    even if monthly profit is positive; the founder must raise cash first.
    self_sustaining opening cash >= 0 and monthly profit >= 0: cash never falls. No runway
                    number applies (runway_months is None).
    finite          opening cash >= 0 and monthly profit < 0, and cash lasts at most
                    PROJECTION_HORIZON_MONTHS. runway_months = floor(opening / burn): the
                    number of FULL months after which cash is still >= 0. A fractional
                    runway rounds down (1.67 -> 1); opening cash of exactly 0 gives 0.
    beyond_horizon  burning, but the runway exceeds PROJECTION_HORIZON_MONTHS
                    (runway_months is None, shown as "24+").
* Scores are kept at full precision for verdict decisions. SCORE_DECIMALS applies to display
  and storage only (scoring.display_score).
"""

from typing import Final

# --- Assumption keys (kept identical to app.llm.schemas.AssumptionKey) ----------------
ASSUMPTION_KEYS: Final[tuple[str, ...]] = (
    "unit_price",
    "variable_cost_per_unit",
    "fixed_monthly_cost",
    "startup_cost",
    "monthly_visitors",
    "visitor_to_order_conversion",
)

# True  = higher value is better for the founder (adverse = low, favorable = high).
# False = cost-like (adverse = high, favorable = low).
ENDPOINT_DIRECTION: Final[dict[str, bool]] = {
    "unit_price": True,
    "variable_cost_per_unit": False,
    "fixed_monthly_cost": False,
    "startup_cost": False,
    "monthly_visitors": True,
    "visitor_to_order_conversion": True,
}

SCENARIOS: Final[tuple[str, ...]] = ("pessimistic", "base", "optimistic")

# --- Projection and simulation ---------------------------------------------------------
PROFIT_MONTH: Final[int] = 6  # month whose cumulative cash defines "profitable by month 6"
PROJECTION_HORIZON_MONTHS: Final[int] = 24  # break-even month and runway search limit
MC_SIMULATIONS: Final[int] = 2000
MC_PERCENTILES: Final[tuple[int, int, int]] = (10, 50, 90)
CASH_EPSILON: Final[float] = 1e-9  # float tolerance for >= comparisons and ceil

# --- Scoring (SRS Section 6) -----------------------------------------------------------
RISK_WEIGHTS: Final[dict[str, float]] = {
    "market_saturation": 0.30,
    "operational_complexity": 0.25,
    "compliance": 0.20,
    "capital_need": 0.15,
    "founder_load": 0.10,
}
SEVERITY_MIN: Final[int] = 1
SEVERITY_MAX: Final[int] = 5
PRELAUNCH_WEIGHTS: Final[dict[str, float]] = {"E": 0.58, "R": 0.42}
SCORE_DECIMALS: Final[int] = 2

# --- Verdict thresholds (SRS Section 6) -------------------------------------------------
GO_OVERALL_MIN: Final[float] = 65.0  # overall >= this (and no weak component) is Go
RETHINK_OVERALL_BELOW: Final[float] = 45.0  # overall < this is Rethink
RETHINK_E_BELOW: Final[float] = 10.0  # E < this is Rethink regardless of overall
COMPONENT_FLOOR: Final[float] = 40.0  # any component < this blocks a plain Go
