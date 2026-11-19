"""Shared helpers for LLM tests: schema-valid payloads and a scripted provider. No network."""

import copy
from typing import Any

from app.llm.base import LLMRequest, LLMResponse


def valid_premortem() -> dict[str, Any]:
    sev = {"severity": 3, "justification": "Moderate risk for a small launch."}
    return {
        "target_customer": "Independent retailers in tier-2 Indian cities",
        "competitors": ["Shiprocket", "Local courier apps", "In-house delivery"],
        "failure_reasons": ["Low density", "Courier reliability", "Thin margins"],
        "compliance_flags": [
            {
                "category": "Consumer protection",
                "note": "Verify with official sources.",
                "verify": True,
            }
        ],
        "risk_severities": {
            k: dict(sev)
            for k in (
                "market_saturation",
                "operational_complexity",
                "compliance",
                "capital_need",
                "founder_load",
            )
        },
        "suggested_pivots": ["Start with one neighbourhood"],
    }


def valid_assumptions() -> dict[str, Any]:
    keys = [
        "unit_price",
        "variable_cost_per_unit",
        "fixed_monthly_cost",
        "startup_cost",
        "monthly_visitors",
        "visitor_to_order_conversion",
    ]
    return {
        "assumptions": [
            {"key": k, "low": 1, "base": 2, "high": 3, "rationale": "Estimate."} for k in keys
        ]
    }


def valid_names() -> dict[str, Any]:
    return {
        "candidates": [
            {"name": f"Name{i:02d}x", "rationale": "Short and memorable."} for i in range(40)
        ]
    }


def valid_content() -> dict[str, Any]:
    return {
        "hero": {
            "headline": "Pool deliveries, keep margin",
            "subhead": "Shared routes.",
            "button_label": "Join waitlist",
        },
        "problem": [
            "Fragmented same-day deliveries",
            "Unpredictable courier cost",
            "Lost repeat customers",
        ],
        "offer": [{"title": f"Benefit {i}", "body": "Plain benefit text."} for i in range(3)],
        "how_it_works": [{"title": f"Step {i}", "body": "Plain step text."} for i in range(3)],
        "proof": {
            "founder_story": "Founded by a retailer.",
            "process": "We plan routes daily.",
            "guarantees": "Cancel anytime.",
            "missing_proof": ["No customer testimonials yet"],
        },
        "pricing": [{"name": "Starter", "price_inr": 999, "bullets": ["Route planning"]}],
        "faq": [{"question": f"Question {i}?", "answer": "Answer."} for i in range(4)],
        "cta": {
            "headline": "Get early access",
            "button_label": "Join waitlist",
            "form_fields": ["email", "name"],
        },
    }


VALID = {
    "premortem": valid_premortem,
    "assumption_extraction": valid_assumptions,
    "business_names": valid_names,
    "website_content": valid_content,
}


def valid_for(task: str) -> dict[str, Any]:
    return copy.deepcopy(VALID[task]())


def tool_response(data: dict[str, Any], **kw: Any) -> LLMResponse:
    return LLMResponse(model="test-model", tool_input=data, stop_reason="tool_use", **kw)


def text_response(text: str, **kw: Any) -> LLMResponse:
    return LLMResponse(model="test-model", text=text, stop_reason="end_turn", **kw)


class ScriptedProvider:
    """Returns/raises scripted outcomes in order and records every request."""

    name = "scripted"

    def __init__(self, *outcomes: LLMResponse | Exception):
        self.outcomes = list(outcomes)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out
