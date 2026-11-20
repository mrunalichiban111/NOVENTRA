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
