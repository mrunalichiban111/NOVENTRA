"""Website rewrite agent (M6: FR-IM-2). One strong-model call through run_structured.

Input: the editable page fields with their limits, the ranked SIMULATED opportunities, and the
founder's answers. All of it is untrusted data inside the delimited block. Output: structured
text replacements for paths only; Python whitelists paths, classifies and validates every item
(app/pipeline/improve.py). The model never writes HTML, links, prices or new facts.
"""

from typing import Any

from app.agents.premortem import RunLLM
from app.llm.schemas import RewriteProposal
from app.llm.structured import run_structured

TASK = "website_rewrite"


def rewrite_prompt(
    fields: list[tuple[str, str, int]],
    opportunities: list[dict[str, Any]],
    answers: list[tuple[str, str]],
) -> str:
    lines = ["CURRENT_TEXT:"]
    lines += [f"{path} (limit {limit}): {text}" for path, text, limit in fields]
    lines.append("")
    lines.append("OPPORTUNITIES (simulated panel, ranked):")
    for o in opportunities:
        lines.append(
            f"- id={o['id']} section={o['section']} type={o['objection_type']} "
            f"evidence={o['evidence_count']} example={o.get('example', '')!r}"
        )
    lines.append("")
    lines.append("FOUNDER_FACTS:")
    lines += [f"- {q} => {a}" for q, a in answers] or ["- (none)"]
    return "\n".join(lines)


def generate_rewrite(
    prompt: str, *, business_id: Any, run_id: str, run_llm: RunLLM = run_structured
) -> tuple[RewriteProposal, str]:
    result = run_llm(TASK, RewriteProposal, prompt, business_id=business_id, run_id=run_id)
    return result.data, result.model
