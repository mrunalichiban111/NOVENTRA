"""Business-name agent (FR-BR-1, FR-BR-3).

Inputs come only from what is already stored for the business (idea, market, the pre-mortem's
target customer and competitors), never from client-supplied replacements. The pre-mortem has
no separate positioning field, so competitors stand in as positioning context. All of it is
untrusted data: run_structured wraps it in a delimited block. The model proposes names and
rationales; Python normalizes, slugs, validates and ranks them. The rationale is never scored.
"""

from pathlib import Path
from typing import Any

from app import config
from app.agents.premortem import RunLLM
from app.llm.scenarios import scenario_for_idea
from app.llm.schemas import BusinessNames
from app.llm.structured import is_mock, run_structured
from app.models import Business
from app.names.normalize import normalize_candidates
from app.names.rank import NameRecord, build_candidates, idea_keywords
from app.names.slug import apex_label

TASK = "business_names"
FIXTURE = "business_names.json"


class NamesInputMissing(ValueError):
    """The business has no stored pre-mortem to name from."""


def names_prompt(business: Business) -> str:
    pm: dict[str, Any] = business.premortem_json or {}
    lines = [
        f"Market: {business.market}",
        f"Target customer: {pm.get('target_customer') or business.target_customer or 'unknown'}",
    ]
    competitors = [str(c) for c in pm.get("competitors") or []]
    if competitors:
        lines.append("Positioning context (alternatives customers use today):")
        lines.extend(f"- {c}" for c in competitors)
    lines.append(f"Idea: {business.idea_text}")
    return "\n".join(lines)


def _scenario(business: Business) -> str | None:
    """Mock mode only: use a scenario's own names fixture when it has one, else the default."""
    if not is_mock():
        return None
    scenario = scenario_for_idea(business.idea_text)
    if scenario and (Path(config.FIXTURES_DIR) / "llm" / scenario / FIXTURE).is_file():
        return scenario
    return None


def generate_names(
    business: Business, *, run_id: str, run_llm: RunLLM = run_structured
) -> list[NameRecord]:
    """Call the model once (with run_structured's repair retries) and return ranked records."""
    if not business.premortem_json:
        raise NamesInputMissing(f"business {business.id} has no stored pre-mortem")
    result = run_llm(
        TASK,
        BusinessNames,
        names_prompt(business),
        business_id=business.id,
        run_id=run_id,
        fixture_scenario=_scenario(business),
    )
    raw = normalize_candidates((c.name, c.rationale) for c in result.data.candidates)
    pm = business.premortem_json
    keywords = idea_keywords(f"{business.idea_text} {pm.get('target_customer') or ''}")
    return build_candidates(raw, keywords=keywords, apex=apex_label(config.BASE_DOMAIN))
