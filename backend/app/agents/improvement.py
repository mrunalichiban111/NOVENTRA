"""Website rewrite agent (M6: FR-IM-1, FR-IM-2). One strong-model call through run_structured.

Input: the editable page fields with their limits, the ranked SIMULATED opportunities with
bounce evidence, the original simulated demand score, persona-fit outcomes, what already
works (positive signals), and the founder's answers. All of it is untrusted data inside the
delimited block. Output: structured text replacements for paths only; Python whitelists paths,
classifies and validates every item (app/pipeline/improve.py). The model never writes HTML,
links, prices or new facts. Every number here is computed in Python, never by a model.
"""

from collections import defaultdict
from typing import Any

from app.agents.premortem import RunLLM
from app.llm.schemas import RewriteProposal
from app.llm.structured import run_structured

TASK = "website_rewrite"


def panel_evidence(evaluation: Any) -> dict[str, Any]:
    """Deterministic summary of the original (complete) evaluation for the prompt."""
    if evaluation is None:
        return {}
    agg = evaluation.aggregate_json or {}
    fit: dict[str, dict[str, int]] = defaultdict(lambda: {"personas": 0, "runs": 0, "converts": 0})
    bounces_by_fit: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for p in agg.get("personas") or []:
        f = p.get("fit") or "unknown"
        fit[f]["personas"] += 1
        for r in p.get("runs") or []:
            fit[f]["runs"] += 1
            fit[f]["converts"] += r.get("decision") == "convert"
            if r.get("decision") == "bounce":
                bounces_by_fit[(r.get("objection_type") or "other", r.get("section") or "none")][
                    f
                ] += 1
    return {
        "d_score": None if evaluation.d_score is None else str(evaluation.d_score),
        "converted": agg.get("converted"),
        "total_runs": agg.get("total_runs"),
        "fit": {k: dict(v) for k, v in sorted(fit.items())},
        "bounces_by_fit": {f"{t}:{s}": dict(v) for (t, s), v in bounces_by_fit.items()},
        "positive_signals": agg.get("positive_signals") or {},
    }


def rewrite_prompt(
    fields: list[tuple[str, str, int]],
    opportunities: list[dict[str, Any]],
    answers: list[tuple[str, str]],
    evidence: dict[str, Any] | None = None,
) -> str:
    ev = evidence or {}
    lines = ["CURRENT_TEXT:"]
    lines += [f"{path} (limit {limit}): {text}" for path, text, limit in fields]
    lines.append("")
    lines.append("PANEL_RESULT (simulated):")
    if ev.get("d_score") is not None:
        lines.append(
            f"- original demand score D={ev['d_score']} "
            f"({ev.get('converted')} of {ev.get('total_runs')} simulated evaluations converted)"
        )
    for f, v in (ev.get("fit") or {}).items():
        lines.append(
            f"- fit={f}: {v['personas']} personas, {v['converts']} of {v['runs']} runs converted"
        )
    if not ev:
        lines.append("- (not available)")
    lines.append("")
    lines.append("OPPORTUNITIES (simulated panel, ranked by bounces; address the top ones first):")
    by_fit = ev.get("bounces_by_fit") or {}
    for o in opportunities:
        fits = by_fit.get(o["id"]) or {}
        fit_txt = ", ".join(f"{k}={n}" for k, n in sorted(fits.items())) or "n/a"
        lines.append(
            f"- id={o['id']} section={o['section']} type={o['objection_type']} "
            f"bounces={o.get('bounces', 0)} evidence={o['evidence_count']} "
            f"personas={o.get('personas', 0)} bounces_by_fit=[{fit_txt}] "
            f"example={o.get('example', '')!r}"
        )
    lines.append("")
    lines.append(
        "WORKING_WELL (sections cited by converting simulated visitors; keep their claims):"
    )
    pos = ev.get("positive_signals") or {}
    lines += [f"- {sec}: {n}" for sec, n in pos.items()] or ["- (none recorded)"]
    lines.append("")
    lines.append("FOUNDER_FACTS:")
    lines += [f"- {q} => {a}" for q, a in answers] or ["- (none)"]
    return "\n".join(lines)


def generate_rewrite(
    prompt: str, *, business_id: Any, run_id: str, run_llm: RunLLM = run_structured
) -> tuple[RewriteProposal, str]:
    result = run_llm(TASK, RewriteProposal, prompt, business_id=business_id, run_id=run_id)
    return result.data, result.model
