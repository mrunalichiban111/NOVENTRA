"""Site model: founder-only pricing, operator links, content hash. Pure: no DB, no network."""

from decimal import Decimal

import pytest

from app.deploy import site_model
from app.deploy.site_model import (
    NO_PRICE_NOTE,
    SiteConfigError,
    SiteLinks,
    build_site_model,
    content_hash,
    format_inr,
    pricing_view,
    site_links,
)
from tests.deploy.helpers import content, max_content


@pytest.mark.parametrize(
    ("amount", "text"),
    [
        ("0.5", "₹0.50"),
        ("999", "₹999"),
        ("1299", "₹1,299"),
        ("125000", "₹1,25,000"),
        ("12345678.9", "₹1,23,45,678.90"),
        ("2499.999", "₹2,500"),
    ],
)
def test_format_inr_uses_indian_grouping(amount: str, text: str) -> None:
    assert format_inr(Decimal(amount)) == text


def test_format_inr_refuses_negative() -> None:
    with pytest.raises(ValueError):
        format_inr(Decimal("-1"))


def test_model_prices_are_ignored_in_favour_of_the_founders() -> None:
    c = content(max_content())  # model plan priced at 10^9
    view = pricing_view(c, Decimal("2499"))
    assert view["plan"] == {
        "name": c.pricing[0].name,
        "price": "₹2,499",
        "bullets": list(c.pricing[0].bullets),
    }
    assert "1,00,00,00,000" not in str(view)


def test_no_founder_price_means_no_plan() -> None:
    assert pricing_view(content(), None) == {"plan": None, "note": NO_PRICE_NOTE}


def test_zero_price_is_shown_as_free() -> None:
    assert pricing_view(content(), Decimal("0"))["plan"]["price"] == "Free"


def test_content_hash_is_canonical_and_versioned(monkeypatch: pytest.MonkeyPatch) -> None:
    a, b = content(), content()
    assert content_hash(a) == content_hash(b) and len(content_hash(a)) == 64
    changed = content()
    changed.hero.headline = "Something else"
    assert content_hash(changed) != content_hash(a)
    before = content_hash(a)
    monkeypatch.setattr(site_model, "TEMPLATE_VERSION", "site-2")
    assert content_hash(a) != before


def test_site_links_accepts_plain_https_and_email() -> None:
    links = site_links(" https://app.example.in/api/public/waitlist ", "abuse@example.in")
    assert links == SiteLinks("https://app.example.in/api/public/waitlist", "abuse@example.in")
    assert links.form_origin == "https://app.example.in"
    assert site_links("", "") == SiteLinks(None, None)


@pytest.mark.parametrize(
    "url",
    [
        "http://app.example.in/w",
        "javascript:alert(1)",
        "https://user:pw@app.example.in/w",
        "https://app.example.in/w?x=1",
        "https://app.example.in/w#frag",
        'https://app.example.in/w"onmouseover=x',
        "https:///nohost",
    ],
)
def test_site_links_rejects_unsafe_waitlist_urls(url: str) -> None:
    with pytest.raises(SiteConfigError):
        site_links(url, "")


@pytest.mark.parametrize("email", ["nope", "a@b", "a b@example.in", "x@example.in?cc=y"])
def test_site_links_rejects_bad_abuse_email(email: str) -> None:
    with pytest.raises(SiteConfigError):
        site_links("", email)


def test_build_site_model_csp_follows_waitlist_config() -> None:
    off = build_site_model(
        content(), business_name="Tiffo", host="tiffo.x.in", price_guess=None, links=SiteLinks()
    )
    assert "form-action 'none'" in off["csp"] and off["waitlist_action"] is None
    on = build_site_model(
        content(),
        business_name="Tiffo",
        host="tiffo.x.in",
        price_guess=None,
        links=SiteLinks("https://app.example.in/api/w", None),
    )
    assert "form-action https://app.example.in;" in on["csp"]
    assert "pricing" not in on["content"]  # model plans never reach the template


@pytest.mark.parametrize(
    ("name", "host"),
    [
        ("", "tiffo.x.in"),
        ("x" * 64, "tiffo.x.in"),
        ("Tiffo <b>", "tiffo.x.in"),
        ("Tiffo", "Tiffo.X.in"),
        ("Tiffo", "../etc"),
        ("Tiffo", "tiffo"),
    ],
)
def test_build_site_model_rejects_bad_name_or_host(name: str, host: str) -> None:
    with pytest.raises(ValueError):
        build_site_model(
            content(), business_name=name, host=host, price_guess=None, links=SiteLinks()
        )
