"""Persona arena agents (M5: FR-AR-1, FR-AR-2). All output is SIMULATED, never market research.

- Panel: ONE call on the strong model, from the idea, market, target customer and price/budget
  context only (untrusted founder text). It never sees the website. Diversity rules are
  validated in code (PersonaPanelOut) and fed back through run_structured's 2 repair retries.
- Evaluation: ALWAYS Claude Haiku (task `persona_evaluation`, tier small). `assert_haiku`
  refuses any other configured model: there is no Gemini path and no provider fallback.
  The prompt is the page's visible text FIRST and the persona batch LAST, both inside the
  untrusted-data block; no WebsiteContent, assessment, pre-mortem or finance data is included.
"""

import hashlib
import json
from typing import Any

from app.agents.premortem import RunLLM
from app.llm.base import ConfigError
from app.llm.routing import get_task, model_for
from app.llm.schemas import PersonaEvaluation, PersonaEvaluationBatch, PersonaPanelOut
from app.llm.structured import PROMPTS_DIR, is_mock, run_structured
from app.models import Business

PANEL_TASK = "persona_panel"
EVAL_TASK = "persona_evaluation"
HAIKU_PREFIX = "claude-haiku-"


class EvaluatorMisconfigured(ConfigError):
    category = "evaluator_misconfigured"


def prompt_version(task: str) -> str:
    """Short hash of the exact system prompt files, so prompt edits break comparability."""
    data = (PROMPTS_DIR / "_preamble.md").read_bytes() + (PROMPTS_DIR / f"{task}.md").read_bytes()
    return hashlib.sha256(data).hexdigest()[:12]


def assert_haiku() -> str:
    """Return the evaluator model id, or raise: persona evaluation runs on Claude Haiku only."""
    if is_mock():
        return "mock"
    model = model_for(get_task(EVAL_TASK))
    if not model.startswith(HAIKU_PREFIX):
        raise EvaluatorMisconfigured(
            f"persona evaluation must run on Claude Haiku; CLAUDE_MODEL_SMALL is {model!r}"
        )
    return model


def panel_prompt(business: Business) -> str:
    pm = business.premortem_json or {}
    target = pm.get("target_customer") or business.target_customer
    price = f"INR {business.price_guess}" if business.price_guess is not None else "not stated"
    budget = (
        f"INR {business.starting_budget}" if business.starting_budget is not None else "not stated"
    )
    return "\n".join(
        [
            f"Market: {business.market}",
            f"Target customer: {target or 'not stated'}",
            f"Founder's intended price: {price}",
            f"Founder's starting budget: {budget}",
            f"Idea: {business.idea_text}",
        ]
    )


def generate_panel(
    business: Business, *, run_id: str, run_llm: RunLLM = run_structured
) -> tuple[list[dict[str, Any]], str]:
    """One strong-model call. Returns (personas with Python-assigned ids p01..p30, model id)."""
    result = run_llm(
        PANEL_TASK, PersonaPanelOut, panel_prompt(business), business_id=business.id, run_id=run_id
    )
    personas = [
        {"id": f"p{i + 1:02d}", **p.model_dump()} for i, p in enumerate(result.data.personas)
    ]
    return personas, result.model


_PERSONA_FIELDS = (
    "id", "name", "segment", "occupation", "age", "budget_tier_inr", "fit", "urgency",
    "skepticism", "tech_comfort", "hidden_objection", "constraints",
)  # fmt: skip


def evaluation_prompt(page: str, personas: list[dict[str, Any]]) -> str:
    """PAGE first (shared prefix across all calls), PERSONAS last."""
    batch = [{k: p[k] for k in _PERSONA_FIELDS if k in p} for p in personas]
    return (
        "PAGE (visible text, in section order):\n"
        f"{page}\n\n"
        "PERSONAS (evaluate each of these ids exactly once):\n"
        f"{json.dumps(batch, ensure_ascii=False, indent=1)}"
    )


def evaluate_batch(
    page: str,
    personas: list[dict[str, Any]],
    *,
    business_id: Any,
    run_id: str,
    run_llm: RunLLM = run_structured,
) -> tuple[list[PersonaEvaluation], str]:
    """One Haiku call for a batch. Returns (evaluations for the requested ids only, model id).
    Unrequested or duplicate ids are dropped; missing ids are simply absent (retried later)."""
    assert_haiku()
    result = run_llm(
        EVAL_TASK,
        PersonaEvaluationBatch,
        evaluation_prompt(page, personas),
        business_id=business_id,
        run_id=run_id,
    )
    wanted = {p["id"] for p in personas}
    seen: set[str] = set()
    kept = []
    for ev in result.data.evaluations:
        if ev.persona_id in wanted and ev.persona_id not in seen:
            seen.add(ev.persona_id)
            kept.append(ev)
    return kept, result.model
