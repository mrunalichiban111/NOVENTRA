"""Shared M4B test data: valid and maximum-length website content. No DB, no network."""

import copy
import json
from pathlib import Path
from typing import Any

from app.llm.schemas import WebsiteContent
from tests.llm_helpers import valid_for

FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "llm" / "content.json"


def fill(limit: int) -> str:
    """Plain words padded to exactly `limit` characters (passes every content rule)."""
    text = ("Plain words here " * (limit // 10 + 2))[:limit]
    return text[:-1] + "x" if text.endswith(" ") else text


def max_content() -> dict[str, Any]:
    """Every field at its Appendix B maximum length and item count."""
    return {
        "hero": {"headline": fill(70), "subhead": fill(160), "button_label": fill(24)},
        "problem": [fill(100) for _ in range(3)],
        "offer": [{"title": fill(40), "body": fill(140)} for _ in range(3)],
        "how_it_works": [{"title": fill(40), "body": fill(120)} for _ in range(3)],
        "proof": {
            "founder_story": fill(300),
            "process": fill(300),
            "guarantees": fill(300),
            "missing_proof": [fill(40) for _ in range(5)],
        },
        "pricing": [
            {"name": fill(40), "price_inr": 10**9, "bullets": [fill(80) for _ in range(4)]}
        ],
        "faq": [{"question": fill(120), "answer": fill(200)} for _ in range(6)],
        "cta": {"headline": fill(70), "button_label": fill(24), "form_fields": ["email", "name"]},
    }


def content(data: dict[str, Any] | None = None) -> WebsiteContent:
    return WebsiteContent.model_validate(data if data is not None else valid_for("website_content"))


def fixture_content() -> WebsiteContent:
    return WebsiteContent.model_validate(json.loads(FIXTURE.read_text(encoding="utf-8")))


def with_field(path: str, value: str) -> dict[str, Any]:
    """A valid payload with one dotted-path string replaced (e.g. 'offer.1.body')."""
    data = copy.deepcopy(valid_for("website_content"))
    node: Any = data
    keys = path.split(".")
    for key in keys[:-1]:
        node = node[int(key)] if isinstance(node, list) else node[key]
    last = keys[-1]
    if isinstance(node, list):
        node[int(last)] = value
    else:
        node[last] = value
    return data


BASE = "example.in"
HOST = "tiffo.example.in"


def rendered(headline: str | None = None, price: str | None = "1299"):  # noqa: ANN201
    """A real rendered site for HOST (optionally with a different headline)."""
    from decimal import Decimal

    from app.deploy.render import render_site
    from app.deploy.site_model import SiteLinks

    c = fixture_content()
    if headline is not None:
        c = c.model_copy(deep=True)
        c.hero.headline = headline
    return render_site(
        c,
        business_name="Tiffo",
        host=HOST,
        price_guess=None if price is None else Decimal(price),
        links=SiteLinks(),
    )
