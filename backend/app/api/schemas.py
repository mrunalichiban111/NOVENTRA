import re
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


class ContactCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=100)
    email: EmailStr
    message: str = Field(min_length=10, max_length=2000)
    website: str = Field(default="", max_length=200)

    @field_validator("name", "email")
    @classmethod
    def _no_control_characters(cls, value: str) -> str:
        if any(ord(char) < 32 or ord(char) == 127 for char in value):
            raise ValueError("must not contain control characters")
        return value

MAX_IDEA_CHARS = 2000
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def count_sentences(text: str) -> int:
    return len([part for part in _SENTENCE_SPLIT.split(text.strip()) if part.strip()])


class BusinessCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idea_text: str = Field(max_length=MAX_IDEA_CHARS)
    market: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=80)
    ] = "India"
    price_guess: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)
    starting_budget: Decimal | None = Field(default=None, ge=0, max_digits=14, decimal_places=2)

    @field_validator("idea_text")
    @classmethod
    def _two_to_five_sentences(cls, value: str) -> str:
        value = value.strip()
        n = count_sentences(value)
        if not 2 <= n <= 5:
            raise ValueError(f"idea_text must be 2-5 sentences (found {n})")
        return value


class BusinessCreated(BaseModel):
    id: uuid.UUID
    status: str


class AuditEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    actor: str
    action: str
    reason: str | None
    version_ref: str | None
    from_status: str | None
    to_status: str | None
    ts: datetime


class PipelineInfo(BaseModel):
    current_stage: str
    next_stage: str | None
    terminal: bool
    in_progress: bool
    attempts: int
    max_attempts: int
    failed_stage: str | None
    last_error: str | None
    retryable: bool
    history: list[AuditEntry]


class AssumptionOut(BaseModel):
    key: str
    low: float
    base: float
    high: float
    rationale: str | None
    source: str  # llm | user


class ScorecardOut(BaseModel):
    version: int
    stage: str
    economics: float  # E, display-rounded; the verdict used the full-precision value
    risk: float  # R
    overall: float
    verdict: str  # go | go_with_changes | rethink
    explanation: list[str]
    next_actions: list[dict[str, Any]]
    risk_severities: dict[str, Any]
    notes: list[str]


class FinanceOut(BaseModel):
    """Deterministic results. Monte Carlo figures are simulated (see notes on the scorecard)."""

    starting_budget: float
    starting_budget_source: str
    scenarios: dict[str, Any]
    monte_carlo: dict[str, Any]
    seed: int


class AssumptionEdit(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: Literal[
        "unit_price",
        "variable_cost_per_unit",
        "fixed_monthly_cost",
        "startup_cost",
        "monthly_visitors",
        "visitor_to_order_conversion",
    ]
    low: float | None = Field(default=None, ge=0, allow_inf_nan=False, le=1e12)
    base: float | None = Field(default=None, ge=0, allow_inf_nan=False, le=1e12)
    high: float | None = Field(default=None, ge=0, allow_inf_nan=False, le=1e12)

    @model_validator(mode="after")
    def _something_to_change(self) -> "AssumptionEdit":
        if self.low is None and self.base is None and self.high is None:
            raise ValueError("provide at least one of low, base, high")
        return self


class AssumptionsPatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assumptions: list[AssumptionEdit] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def _unique_keys(self) -> "AssumptionsPatch":
        keys = [a.key for a in self.assumptions]
        if len(set(keys)) != len(keys):
            raise ValueError("each assumption key may appear only once")
        return self


class BusinessDetail(BaseModel):
    id: uuid.UUID
    idea_text: str
    market: str
    price_guess: Decimal | None
    starting_budget: Decimal | None
    target_customer: str | None
    status: str
    domain: str | None
    created_at: datetime
    updated_at: datetime
    pipeline: PipelineInfo
    rejection_reason: str | None = None  # safety rejection (REJECTED), not a commercial verdict
    premortem: dict[str, Any] | None = None
    assumptions: list[AssumptionOut] = []
    finance: FinanceOut | None = None
    scorecard: ScorecardOut | None = None
    verdict: str | None = None
    verdict_explanation: list[str] = []


# --- M4A: business names and subdomain claims (FR-BR-1..5) ----------------------------

NAMES_DISCLAIMER = (
    "Names are ranked by a simple heuristic on the web address only. This is not a trademark, "
    "legal-clearance or domain-registration check."
)


class GenerateNamesRequest(BaseModel):
    """Empty by design: inputs come from the stored analysis, never from the client."""

    model_config = ConfigDict(extra="forbid")


class DomainCheckOut(BaseModel):
    domain: str
    tld: str
    status: Literal["AVAILABLE", "UNAVAILABLE", "UNKNOWN"]
    reason: str | None = None
    message: str
    raw_price: Decimal | None = None  # registrar's raw value; units unverified, not a quote
    currency: str | None = None
    definitive: bool | None = None
    checked_at: datetime | None = None
    cached: bool = False


class NameCandidateOut(BaseModel):
    id: uuid.UUID
    name: str
    slug: str | None
    rank: int | None
    score_total: Decimal | None
    score_factors: dict[str, Any]
    valid: bool
    invalid_reason: str | None
    rationale: str | None
    subdomain_available: bool
    subdomain_host: str | None
    selected: bool
    domain_checks: list[DomainCheckOut] = []


class NamesList(BaseModel):
    business_id: uuid.UUID
    status: str
    subdomains_configured: bool
    generations_used: int
    generations_max: int
    disclaimer: str = NAMES_DISCLAIMER
    candidates: list[NameCandidateOut]


class NamesGenerated(NamesList):
    run_id: str
    replayed: bool


class AvailabilityOut(BaseModel):
    candidate_id: uuid.UUID
    slug: str
    results: list[DomainCheckOut]
    disclaimer: str = (
        "Read-only check. NOVENTRA never buys domains. 'Likely available' must be verified on "
        "GoDaddy; prices are the registrar's raw values and are not a quote."
    )


class NameSelected(BaseModel):
    business_status: str
    subdomain_host: str | None
    changed: bool
    replaced_slug: str | None
    candidate: NameCandidateOut


# --- M4B: site deployment ----------------------------------------------------------------


class DeployRequested(BaseModel):
    business_id: uuid.UUID
    status: str
    host: str | None
    changed: bool  # False: already deploying or live (idempotent repeat)


class SiteVersionOut(BaseModel):
    version: int
    status: str
    content_hash: str | None
    artifact_hash: str | None
    parent_version: int | None
    generation_attempt: int
    error: str | None
    deployed_at: datetime | None
    created_at: datetime


class SiteOut(BaseModel):
    business_id: uuid.UUID
    status: str
    host: str | None
    url: str | None
    live_version: int | None
    verification_level: str | None  # simulated | local | public
    publicly_reachable: bool  # True only for `public` verification
    versions: list[SiteVersionOut]


class DeploymentOut(BaseModel):
    id: uuid.UUID
    version: int
    attempt: int
    host: str
    provider: str
    status: str
    verification_level: str | None
    publicly_reachable: bool
    error: str | None
    checks: list[dict[str, Any]]
    started_at: datetime
    finished_at: datetime | None


class DeploymentsList(BaseModel):
    business_id: uuid.UUID
    deployments: list[DeploymentOut]
