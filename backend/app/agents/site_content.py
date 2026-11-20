"""Website content agent (FR-SITE-1, FR-SITE-8).

The prompt is built only from facts already stored for the business: the idea text, market,
the pre-mortem's target customer, whether the founder gave a price, and the selected business
name. Competitors are deliberately left out so the page never names them. All of it is
untrusted data: run_structured wraps it in a delimited block. The model returns plain-text
section content validated by `WebsiteContent` (lengths plus content_rules); it never chooses
the name, the host, links, prices or markup. Python applies the founder's price later
(app/deploy/site_model.py).
"""

from pathlib import Path

from app import config
from app.agents.premortem import RunLLM
from app.llm.scenarios import scenario_for_idea
from app.llm.schemas import WebsiteContent
from app.llm.structured import is_mock, run_structured
from app.models import Business

TASK = "website_content"
FIXTURE = "content.json"


class SiteInputMissing(ValueError):
    """The business lacks a fact the site cannot be generated without."""


def content_prompt(business: Business, business_name: str) -> str:
    pm = business.premortem_json or {}
    target = pm.get("target_customer") or business.target_customer
    lines = [
        f"Business name: {business_name}",
        f"Market: {business.market}",
        f"Target customer: {target or 'not stated'}",
        (
            "Founder's price: stated (the system will show it)"
            if business.price_guess is not None
            else "Founder's price: not stated (the page will say pricing comes before launch)"
        ),
        f"Idea: {business.idea_text}",
    ]
    return "\n".join(lines)


def _scenario(business: Business) -> str | None:
    """Mock mode only: use a scenario's own content fixture when it has one, else the default."""
    if not is_mock():
        return None
    scenario = scenario_for_idea(business.idea_text)
    if scenario and (Path(config.FIXTURES_DIR) / "llm" / scenario / FIXTURE).is_file():
        return scenario
    return None


def generate_site_content(
    business: Business,
    *,
    business_name: str,
    run_id: str,
    run_llm: RunLLM = run_structured,
) -> WebsiteContent:
    """One model call (with run_structured's repair retries). Returns validated content."""
    if not business_name or not business_name.strip():
        raise SiteInputMissing(f"business {business.id} has no selected business name")
    if not business.idea_text or not business.idea_text.strip():
        raise SiteInputMissing(f"business {business.id} has no idea text")
    result = run_llm(
        TASK,
        WebsiteContent,
        content_prompt(business, business_name.strip()),
        business_id=business.id,
        run_id=run_id,
        fixture_scenario=_scenario(business),
    )
    return result.data
