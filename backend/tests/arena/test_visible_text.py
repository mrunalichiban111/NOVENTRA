"""Visible-text extraction from the rendered release (FR-AR-2). Pure: no DB, no network."""

from decimal import Decimal

from app.deploy.render import render_site
from app.deploy.site_model import SiteLinks
from app.deploy.visible_text import extract_visible_text, page_text
from tests.deploy.helpers import fixture_content

LINKS = SiteLinks("https://app.example.in/api/public/waitlist", "abuse@example.in")
ORDER = ["header", "hero", "problem", "offer", "how_it_works", "proof", "pricing", "faq", "cta",
         "footer"]  # fmt: skip


def _html() -> str:
    site = render_site(
        fixture_content(), business_name="PuneBite", host="punebite.example.in",
        price_guess=Decimal("149"), links=LINKS,
    )  # fmt: skip
    return site.files["index.html"].decode("utf-8")


def test_sections_in_page_order_with_template_text() -> None:
    sections = extract_visible_text(_html())
    names = [s for s, _ in sections]
    assert [n for n in names if n in ORDER] == ORDER
    text = dict(sections)
    c = fixture_content()
    assert c.hero.headline in text["hero"]
    assert c.faq[0].question in text["faq"]
    assert "PuneBite" in text["header"] and "Coming soon" in text["header"]
    assert "Email" in text["cta"]  # template-added label a visitor sees
    assert "has not launched yet" in text["footer"]


def test_hidden_script_style_and_honeypot_skipped() -> None:
    html = _html().replace(
        "</main>",
        "<script>alert('x')</script><style>.a{}</style><noscript>NS</noscript>"
        '<div hidden>HIDDEN</div><p aria-hidden="true">ARIA</p></main>',
    )
    joined = page_text(extract_visible_text(html))
    for bad in ("alert", ".a{}", "NS", "HIDDEN", "ARIA", "Leave this field empty"):
        assert bad not in joined
    assert "<" not in joined and "Content-Security-Policy" not in joined


def test_theme_attributes_do_not_change_text() -> None:
    html = _html()
    themed = html.replace('<section class="hero"', '<section class="hero theme-warm" data-x="1"')
    assert extract_visible_text(themed) == extract_visible_text(html)


def test_page_text_format() -> None:
    out = page_text([("hero", "Hi"), ("faq", "Q")])
    assert out == "[hero]\nHi\n\n[faq]\nQ"
