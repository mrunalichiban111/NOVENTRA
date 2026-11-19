"""Pre-mortem and assumption extraction agents (FR-PM-1..4, FR-FIN-1).

Both go through `run_structured` (schema-validated, <=2 repair retries, budget guard, usage
tracking). The idea and the founder's numbers are untrusted data: run_structured wraps the
prompt in a delimited block. Python adds the labels the SRS requires (competitors unverified,
verify-with-official-sources notes, not-legal-advice disclaimer) so they never depend on the
model remembering them. The model never computes a score.
"""

from collections.abc import Callable
from typing import Any

from app.llm.base import LLMError
from app.llm.scenarios import scenario_for_idea
from app.llm.schemas import Assumptions, PreMortem
from app.llm.structured import StructuredResult, is_mock, run_structured
from app.models import Business

RunLLM = Callable[..., StructuredResult[Any]]

DISCLAIMER = (
    "These compliance flags are prompts to check, not legal advice. "
    "Verify each one with the official government or regulator source before acting."
)
VERIFY_NOTE = "Verify with official sources before relying on this. Not legal advice."
COMPETITOR_LABEL = "unverified"


def idea_prompt(business: Business) -> str:
    """Founder-supplied facts as plain text for the untrusted-data block."""
    lines = [f"Market: {business.market}"]
    if business.price_guess is not None:
        lines.append(f"Founder's price guess (INR): {business.price_guess}")
    if business.starting_budget is not None:
        lines.append(f"Founder's starting budget (INR): {business.starting_budget}")
    lines.append(f"Idea: {business.idea_text}")
    return "\n".join(lines)


def _scenario(business: Business) -> str | None:
    # Mock mode only; run_structured discards it for real calls.
    return scenario_for_idea(business.idea_text) if is_mock() else None


def run_premortem(business: Business, *, run_id: str, run_llm: RunLLM = run_structured) -> dict:
    """Return the JSON-ready pre-mortem to store (validated model output plus Python labels)."""
    result = run_llm(
        "premortem",
        PreMortem,
        idea_prompt(business),
        business_id=business.id,
        run_id=run_id,
        fixture_scenario=_scenario(business),
    )
    return to_stored(result.data)


def to_stored(pm: PreMortem) -> dict:
    data = pm.model_dump()
    data["competitors"] = [
        {"name": name, "verified": False, "label": COMPETITOR_LABEL} for name in pm.competitors
    ]
    data["compliance_flags"] = [
        {**flag, "verify": True, "verification_note": VERIFY_NOTE}
        for flag in data["compliance_flags"]
    ]
    data["disclaimer"] = DISCLAIMER
    data["source"] = "llm"
    return data


def run_assumption_extraction(
    business: Business, *, run_id: str, run_llm: RunLLM = run_structured
) -> Assumptions:
    result = run_llm(
        "assumption_extraction",
        Assumptions,
        idea_prompt(business),
        business_id=business.id,
        run_id=run_id,
        fixture_scenario=_scenario(business),
    )
    return result.data


__all__ = ["LLMError", "run_premortem", "run_assumption_extraction", "to_stored", "idea_prompt"]
