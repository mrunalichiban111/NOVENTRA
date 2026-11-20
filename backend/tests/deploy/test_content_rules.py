"""WebsiteContent safety rules (FR-SITE-1, FR-SITE-6, FR-SITE-8). Pure: no DB, no network."""

import pytest
from pydantic import ValidationError

from app.llm import structured
from app.llm.content_rules import text_problems
from app.llm.schemas import WebsiteContent
from tests.deploy.helpers import content, fixture_content, max_content, with_field
from tests.llm_helpers import ScriptedProvider, tool_response, valid_for


def test_fixture_helper_and_max_length_content_pass() -> None:
    fixture_content()
    content()
    content(max_content())


@pytest.mark.parametrize(
    "text",
    [
        "<script>alert(1)</script>",
        "Bold <b>claims</b>",
        "a > b",
        "&#60;script&#62;",
        "&lt;b&gt;",
        "{{ config }}",
        "{% include x %}",
    ],
)
def test_markup_rejected(text: str) -> None:
    with pytest.raises(ValidationError, match="hero.subhead: contains markup"):
        content(with_field("hero.subhead", text))


@pytest.mark.parametrize(
    "text",
    [
        "Visit https://evil.example now",
        "See www.example.org",
        "Click javascript:alert(1)",
        "data:text/html,hi",
        "Order at tiffo.com today",
        "Write to hello@tiffo.in",
        "Call tel:+911234",
        "ftp://files",
    ],
)
def test_links_and_emails_rejected(text: str) -> None:
    with pytest.raises(ValidationError, match="offer.1.body: contains a link"):
        content(with_field("offer.1.body", text))


@pytest.mark.parametrize(
    "text",
    [
        "Rated 4.9 by parents",
        "4.8/5 average",
        "Five stars from everyone",
        "Read our testimonials",
        "Great reviews so far",
        "Trusted by local shops",
        "As seen in the news",
        "Award-winning service",
        "The #1 tiffin in Pune",
        "Saves 30% on costs",
        "Customers love it",
    ],
)
def test_fabricated_proof_claims_rejected(text: str) -> None:
    with pytest.raises(ValidationError, match="proof.founder_story: contains a rating"):
        content(with_field("proof.founder_story", text))


@pytest.mark.parametrize(
    "text", ["Over 500 happy customers", "2k users already", "10 lakh families", "1,200 shops"]
)
def test_customer_counts_rejected(text: str) -> None:
    with pytest.raises(ValidationError, match="faq.0.answer: claims a number of customers"):
        content(with_field("faq.0.answer", text))


@pytest.mark.parametrize("text", ["Hidden​char", "Bidi ‮override", "Bell \x07", "x\ny"])
def test_invisible_and_control_characters_rejected(text: str) -> None:
    with pytest.raises(ValidationError, match="control or invisible"):
        content(with_field("hero.headline", text))


def test_missing_proof_may_name_absent_proof_but_not_links() -> None:
    data = valid_for("website_content")
    data["proof"]["missing_proof"] = ["No customer testimonials yet", "No reviews or ratings yet"]
    content(data)
    data["proof"]["missing_proof"] = ["See https://x.example"]
    with pytest.raises(ValidationError, match="proof.missing_proof.0: contains a link"):
        content(data)


def test_pricing_bullets_may_state_plan_limits_but_not_proof() -> None:
    data = valid_for("website_content")
    data["pricing"][0]["bullets"] = ["Up to 3 users", "Up to 100 orders a month"]
    content(data)
    data["pricing"][0]["bullets"] = ["Trusted by 40 shops"]
    with pytest.raises(ValidationError, match="pricing.0.bullets.0"):
        content(data)


def test_plain_ampersands_and_numbers_are_fine() -> None:
    assert text_problems("Tea & snacks, delivered by 9 am in 2 steps") == []


def test_error_messages_never_echo_the_value() -> None:
    secret = "Visit https://attacker.example/steal"
    with pytest.raises(ValidationError) as ei:
        content(with_field("hero.headline", secret))
    assert "attacker" not in str(ei.value.errors()[0]["msg"])


def test_rule_violation_triggers_a_repair_retry() -> None:
    bad = with_field("cta.headline", "Join 500+ happy customers")
    p = ScriptedProvider(tool_response(bad), tool_response(valid_for("website_content")))
    result = structured.run_structured(
        "website_content", WebsiteContent, "idea", provider=p, run_id="r1", sleep=lambda _s: None
    )
    assert len(p.requests) == 2
    feedback = p.requests[1].repair_feedback
    assert "cta.headline" in feedback and "500" not in feedback
    assert result.data.cta.headline == "Get early access"


@pytest.mark.parametrize(
    "text",
    [
        "We review every order before it leaves",
        "Our cooks are rising stars",
        "100% vegetarian meals",
        "We have no reviews yet",
        "Not rated yet",
        "We do not have customer testimonials yet",
        "Used by our kitchen team daily",
        "Award ceremony catering on request",
        "We aim to rate each cook fairly",
        "Top up your plan anytime",
    ],
)
def test_ordinary_wording_and_honest_absence_are_allowed(text: str) -> None:
    assert text_problems(text) == []


@pytest.mark.parametrize(
    "text",
    [
        "Five-star service",
        "Highly rated cooks",
        "Hundreds of reviews",
        "100% satisfaction",
        "95% of customers return",
        "Used by 500 families",
        "Won an award last year",
        "Featured in The Hindu",
        "Backed by investors",
        # negation only excuses an immediately negated claim, not a nearby "no"/"not"
        "No one beats our 5-star reviews",
        "Not just great reviews",
        "Not only top rated cooks",
    ],
)
def test_context_aware_claims_still_rejected(text: str) -> None:
    assert any("never invent proof" in p for p in text_problems(text))


def test_exactly_one_pricing_plan() -> None:
    data = valid_for("website_content")
    data["pricing"] = data["pricing"] * 2
    with pytest.raises(ValidationError, match="pricing"):
        content(data)
