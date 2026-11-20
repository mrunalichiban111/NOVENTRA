"""Pydantic output schemas for model-generated content (SRS Appendix B and C).

These validate Claude output and the mock fixtures alike. Everything here is model
output: it is data to be validated, never executed or rendered as HTML. Numbers in
`Assumption` are model *estimates* that feed the deterministic finance engine (M3);
the model never does the arithmetic.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.llm.content_rules import check_fields


class _Out(BaseModel):
    model_config = ConfigDict(extra="forbid")


# --- Pre-mortem (FR-PM-1, Appendix C) -------------------------------------------------


class ComplianceFlag(_Out):
    category: str = Field(min_length=1, max_length=80)
    note: str = Field(min_length=1, max_length=300)
    verify: bool  # always true in practice: flags are never legal advice (FR-PM-3)


class RiskSeverity(_Out):
    severity: int = Field(ge=1, le=5)
    justification: str = Field(min_length=1, max_length=300)


class RiskSeverities(_Out):
    market_saturation: RiskSeverity
    operational_complexity: RiskSeverity
    compliance: RiskSeverity
    capital_need: RiskSeverity
    founder_load: RiskSeverity


class PreMortem(_Out):
    target_customer: str = Field(min_length=1, max_length=300)
    competitors: list[str] = Field(min_length=3, max_length=3)
    failure_reasons: list[str] = Field(min_length=3, max_length=3)
    compliance_flags: list[ComplianceFlag] = Field(max_length=6)
    risk_severities: RiskSeverities
    suggested_pivots: list[str] = Field(min_length=1, max_length=5)


# --- Assumption extraction (FR-FIN-1) -------------------------------------------------

AssumptionKey = Literal[
    "unit_price",
    "variable_cost_per_unit",
    "fixed_monthly_cost",
    "startup_cost",
    "monthly_visitors",
    "visitor_to_order_conversion",
]


class AssumptionItem(_Out):
    key: AssumptionKey
    low: float = Field(ge=0)
    base: float = Field(ge=0)
    high: float = Field(ge=0)
    rationale: str = Field(min_length=1, max_length=200)

    @model_validator(mode="after")
    def _ordered(self) -> "AssumptionItem":
        if not self.low <= self.base <= self.high:
            raise ValueError(f"{self.key}: need low <= base <= high")
        return self


class Assumptions(_Out):
    assumptions: list[AssumptionItem] = Field(min_length=6, max_length=6)

    @model_validator(mode="after")
    def _all_keys_once(self) -> "Assumptions":
        keys = [a.key for a in self.assumptions]
        if len(set(keys)) != 6:
            raise ValueError("each of the six assumption keys must appear exactly once")
        return self


# --- Business names (FR-BR-1) ---------------------------------------------------------


class NameCandidate(_Out):
    name: str = Field(min_length=2, max_length=30)
    rationale: str = Field(min_length=1, max_length=100)


class BusinessNames(_Out):
    candidates: list[NameCandidate] = Field(min_length=40, max_length=60)


# --- Website content (FR-SITE-1, Appendix B limits) -----------------------------------


class Hero(_Out):
    headline: str = Field(min_length=1, max_length=70)
    subhead: str = Field(min_length=1, max_length=160)
    button_label: str = Field(min_length=1, max_length=24)


class TitledBody140(_Out):
    title: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=1, max_length=140)


class TitledBody120(_Out):
    title: str = Field(min_length=1, max_length=40)
    body: str = Field(min_length=1, max_length=120)


class Proof(_Out):
    """Honest trust elements only. List what real proof is still missing instead of inventing it."""

    founder_story: str = Field(max_length=300)
    process: str = Field(max_length=300)
    guarantees: str = Field(max_length=300)
    missing_proof: list[str] = Field(max_length=5)


class PricingPlan(_Out):
    name: str = Field(min_length=1, max_length=40)
    price_inr: int = Field(ge=0)
    bullets: list[str] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def _bullet_len(self) -> "PricingPlan":
        if any(not b or len(b) > 80 for b in self.bullets):
            raise ValueError("pricing bullets must be 1-80 characters")
        return self


class FaqItem(_Out):
    question: str = Field(min_length=1, max_length=120)
    answer: str = Field(min_length=1, max_length=200)


class Cta(_Out):
    headline: str = Field(min_length=1, max_length=70)
    button_label: str = Field(min_length=1, max_length=24)
    form_fields: list[Literal["email", "name"]] = Field(min_length=1, max_length=2)


class WebsiteContent(_Out):
    hero: Hero
    problem: list[str] = Field(min_length=3, max_length=3)
    offer: list[TitledBody140] = Field(min_length=3, max_length=3)
    how_it_works: list[TitledBody120] = Field(min_length=3, max_length=3)
    proof: Proof
    pricing: list[PricingPlan] = Field(min_length=1, max_length=1)  # one plan (M4B)
    faq: list[FaqItem] = Field(min_length=4, max_length=6)
    cta: Cta

    @model_validator(mode="after")
    def _problem_len(self) -> "WebsiteContent":
        if any(not p or len(p) > 100 for p in self.problem):
            raise ValueError("problem pain points must be 1-100 characters")
        return self

    @model_validator(mode="after")
    def _safe_text(self) -> "WebsiteContent":
        """Plain text only: no markup, links or invented proof (app/llm/content_rules.py)."""
        problems = check_fields(self.text_fields())
        if problems:
            more = len(problems) - 6
            raise ValueError("; ".join(problems[:6]) + (f"; and {more} more" if more > 0 else ""))
        return self

    def text_fields(self) -> list[tuple[str, str, bool, bool]]:
        """(path, text, check_proof_claims, check_counts) for every model-written string.

        missing_proof deliberately names absent proof ("No customer testimonials yet"), so only
        markup/link rules apply there. Pricing bullets may state plan limits ("Up to 3 users").
        """
        h, p, c = self.hero, self.proof, self.cta
        out = [
            ("hero.headline", h.headline, True, True),
            ("hero.subhead", h.subhead, True, True),
            ("hero.button_label", h.button_label, True, True),
            ("proof.founder_story", p.founder_story, True, True),
            ("proof.process", p.process, True, True),
            ("proof.guarantees", p.guarantees, True, True),
            ("cta.headline", c.headline, True, True),
            ("cta.button_label", c.button_label, True, True),
        ]
        out += [(f"problem.{i}", t, True, True) for i, t in enumerate(self.problem)]
        for name, items in (("offer", self.offer), ("how_it_works", self.how_it_works)):
            for i, item in enumerate(items):
                out += [
                    (f"{name}.{i}.title", item.title, True, True),
                    (f"{name}.{i}.body", item.body, True, True),
                ]
        out += [
            (f"proof.missing_proof.{i}", t, False, False) for i, t in enumerate(p.missing_proof)
        ]
        for i, plan in enumerate(self.pricing):
            out.append((f"pricing.{i}.name", plan.name, True, True))
            out += [
                (f"pricing.{i}.bullets.{j}", b, True, False) for j, b in enumerate(plan.bullets)
            ]
        for i, item in enumerate(self.faq):
            out += [
                (f"faq.{i}.question", item.question, True, True),
                (f"faq.{i}.answer", item.answer, True, True),
            ]
        return out


# Task name -> output schema. Keep in sync with routing.TASKS.
OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
    "premortem": PreMortem,
    "assumption_extraction": Assumptions,
    "business_names": BusinessNames,
    "website_content": WebsiteContent,
}


# --- Persona arena (M5: FR-AR-1, FR-AR-2, Appendix C) ---------------------------------------
# Everything here is SIMULATED. Persona ids are assigned in Python (p01..p30), never by the model.

PANEL_SIZE = 30  # target size; the model is asked for exactly this many
MIN_PANEL_SIZE = 25  # smallest panel accepted (hackathon decision); never padded or invented
BudgetTier = Literal["under_500", "500_2k", "2k_5k", "5k_15k", "over_15k"]
Fit = Literal["target", "adjacent", "weak", "non_target"]
ObjectionType = Literal["missing_info", "price", "trust", "fit", "clarity", "other"]
PageSection = Literal[
    "header", "hero", "problem", "offer", "how_it_works", "proof", "pricing", "faq", "cta",
    "footer", "none",
]  # fmt: skip


class Persona(_Out):
    name: str = Field(min_length=1, max_length=40)
    segment: str = Field(min_length=1, max_length=60)
    occupation: str = Field(min_length=1, max_length=60)
    age: int = Field(ge=18, le=80)
    budget_tier_inr: BudgetTier
    fit: Fit
    urgency: int = Field(ge=1, le=5)
    skepticism: int = Field(ge=1, le=5)
    tech_comfort: int = Field(ge=1, le=5)
    hidden_objection: str = Field(min_length=1, max_length=120)
    constraints: list[str] = Field(max_length=2)

    @model_validator(mode="after")
    def _constraint_len(self) -> "Persona":
        if any(not c or len(c) > 80 for c in self.constraints):
            raise ValueError("each constraint must be 1-80 characters")
        return self


class PersonaPanelOut(_Out):
    personas: list[Persona] = Field(min_length=MIN_PANEL_SIZE, max_length=PANEL_SIZE)

    @model_validator(mode="after")
    def _diversity(self) -> "PersonaPanelOut":
        """Panel diversity rules (validated in code; failures become repair feedback)."""
        ps = self.personas
        problems = []
        names = [p.name.strip().lower() for p in ps]
        if len(set(names)) != len(names):
            problems.append("persona names must be unique")
        # Thresholds scale with the actual panel size n (equal to 5 / 4 / 12 at n = 30).
        n = len(ps)
        min_skeptic = -(-n // 6)
        min_weak = -(-2 * n // 15)
        max_tier = (2 * n) // 5
        if sum(p.skepticism >= 4 for p in ps) < min_skeptic:
            problems.append(f"at least {min_skeptic} personas need skepticism 4 or 5")
        if sum(p.fit in ("weak", "non_target") for p in ps) < min_weak:
            problems.append(f"at least {min_weak} personas must have fit 'weak' or 'non_target'")
        tiers: dict[str, int] = {}
        for p in ps:
            tiers[p.budget_tier_inr] = tiers.get(p.budget_tier_inr, 0) + 1
        if len(tiers) < 3:
            problems.append("use at least 3 different budget_tier_inr values")
        if max(tiers.values()) > max_tier:
            problems.append(
                f"no single budget_tier_inr may be used by more than {max_tier} personas"
            )
        if problems:
            raise ValueError("; ".join(problems))
        return self


class PersonaEvaluation(_Out):
    persona_id: str = Field(min_length=1, max_length=8)
    decision: Literal["convert", "bounce"]
    confidence: float = Field(ge=0, le=1)
    objection_type: ObjectionType
    top_objection: str = Field(max_length=100)
    section: PageSection
    missing_info: list[str] = Field(max_length=2)
    reason: str = Field(min_length=1, max_length=120)

    @model_validator(mode="after")
    def _missing_len(self) -> "PersonaEvaluation":
        if any(not m or len(m) > 60 for m in self.missing_info):
            raise ValueError("each missing_info item must be 1-60 characters")
        return self


class PersonaEvaluationBatch(_Out):
    # Up to the panel size so one recorded mock fixture serves every batch; the arena keeps only
    # the persona ids it asked for (missing ones are retried, extras are dropped).
    evaluations: list[PersonaEvaluation] = Field(min_length=1, max_length=PANEL_SIZE)


# --- Improvement studio (M6: FR-IM-2) ---------------------------------------------------------
# The model proposes replacement TEXT for whitelisted WebsiteContent paths only. Python decides
# which paths are allowed, classifies every item and validates the result with WebsiteContent.


class RewriteItem(_Out):
    path: str = Field(min_length=3, max_length=40)
    new_text: str = Field(min_length=1, max_length=300)
    rationale: str = Field(min_length=1, max_length=160)
    opportunity_id: str = Field(max_length=60)


class RewriteProposal(_Out):
    items: list[RewriteItem] = Field(min_length=1, max_length=8)


OUTPUT_SCHEMAS.update(
    {
        "persona_panel": PersonaPanelOut,
        "persona_evaluation": PersonaEvaluationBatch,
        "website_rewrite": RewriteProposal,
    }
)
