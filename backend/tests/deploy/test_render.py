"""Renderer: max-length content, escaping, determinism, accessibility basics (SRS §10).

Pure: no DB, no network, nothing written to disk.
"""

import hashlib
import json
import re
from decimal import Decimal
from html.parser import HTMLParser

import pytest

from app.deploy.render import render_site
from app.deploy.site_model import SiteLinks
from app.llm.schemas import WebsiteContent
from tests.deploy.helpers import content, fixture_content, max_content
from tests.llm_helpers import valid_for

LINKS = SiteLinks("https://app.example.in/api/public/waitlist", "abuse@example.in")


def render(c: WebsiteContent | None = None, links: SiteLinks = LINKS, price="1299", **kw):  # noqa: ANN001, ANN003, ANN201
    return render_site(
        c or fixture_content(),
        business_name=kw.pop("business_name", "LocalLoop"),
        host=kw.pop("host", "localloop.example.in"),
        price_guess=None if price is None else Decimal(price),
        links=links,
    )


def html(site) -> str:  # noqa: ANN001
    return site.files["index.html"].decode("utf-8")


class Tags(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, dict[str, str | None]]] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        self.tags.append((tag, dict(attrs)))


def tags(text: str) -> list[tuple[str, dict]]:
    p = Tags()
    p.feed(text)
    return p.tags


def test_maximum_length_content_renders_and_validates() -> None:
    site = render(content(max_content()))
    page = html(site)
    assert max_content()["proof"]["founder_story"] in page
    assert len(site.files["index.html"]) < 64 * 1024


def test_output_is_deterministic() -> None:
    a, b = render(), render()
    assert a.files == b.files and a.artifact_hash == b.artifact_hash


def test_manifest_hashes_every_file() -> None:
    site = render()
    manifest = json.loads(site.files["manifest.json"])
    assert manifest == site.manifest
    assert set(manifest["files"]) == {"index.html", "styles.css"}
    for name, entry in manifest["files"].items():
        assert entry["sha256"] == hashlib.sha256(site.files[name]).hexdigest()
        assert entry["bytes"] == len(site.files[name])
    assert site.artifact_hash == hashlib.sha256(site.files["manifest.json"]).hexdigest()
    assert manifest["content_hash"] == site.content_hash


def test_artifact_changes_with_name_or_price_but_content_hash_does_not() -> None:
    base = render()
    other = render(business_name="Other Name", price="1500")
    assert other.artifact_hash != base.artifact_hash
    assert other.content_hash == base.content_hash


def test_no_scripts_or_external_resources() -> None:
    page = html(render())
    assert "<script" not in page.lower()
    for tag, attrs in tags(page):
        assert not any(k.startswith("on") for k in attrs), tag
        assert "style" not in attrs
        href = attrs.get("href")
        if href is not None:
            assert href.startswith("#") or href in ("styles.css", "mailto:abuse@example.in")
    assert "url(" not in render().files["styles.css"].decode()


def test_accessibility_basics() -> None:
    page = html(render())
    found = tags(page)
    names = [t for t, _ in found]
    assert ("html", {"lang": "en"}) in found
    assert names.count("h1") == 1 and names.count("main") == 1
    assert {"header", "footer", "section"} <= set(names)
    assert ("meta", {"name": "viewport", "content": "width=device-width, initial-scale=1"}) in found
    labels = {a["for"] for t, a in found if t == "label"}
    inputs = {a["id"] for t, a in found if t == "input" and a.get("type") != "hidden"}
    assert inputs and inputs <= labels  # every visible input has a label
    sections = [a for t, a in found if t == "section"]
    assert all("aria-labelledby" in a for a in sections)


def test_waitlist_form_and_honeypot_only_when_configured() -> None:
    on = html(render())
    form = [a for t, a in tags(on) if t == "form"]
    assert form == [{"class": "waitlist", "action": LINKS.waitlist_action, "method": "post"}]
    assert 'name="website"' in on and 'tabindex="-1"' in on  # honeypot
    assert 'name="site" value="localloop.example.in"' in on
    off = html(render(links=SiteLinks()))
    assert "<form" not in off and "waitlist opens soon" in off
    assert "form-action &#39;none&#39;" in off
    assert "mailto:" not in off  # abuse link omitted when not configured


def test_optional_name_field_follows_cta_form_fields() -> None:
    data = valid_for("website_content")
    data["cta"]["form_fields"] = ["email"]
    page = html(render(content(data)))
    assert 'id="wl-email"' in page and 'id="wl-name"' not in page


def test_founder_price_rendered_and_model_price_absent() -> None:
    page = html(render(price="1299"))
    assert "₹1,299" in page and "999" not in page.replace("₹1,299", "")
    no_price = html(render(price=None))
    assert "Pricing will be shared before launch." in no_price
    assert 'class="price"' not in no_price


def test_missing_proof_is_flagged_honestly() -> None:
    page = html(render())
    assert "What we cannot show yet" in page
    assert "No customer testimonials yet" in page


def test_text_is_autoescaped_even_if_validation_were_bypassed() -> None:
    # model_construct skips the content rules: the renderer must still escape everything.
    data = valid_for("website_content")
    evil = WebsiteContent.model_validate(data).model_copy(deep=True)
    evil.hero.headline = '<script>alert("x")</script>'
    evil.faq[0].answer = '"><img src=x onerror=alert(1)>'
    page = html(render(evil))
    assert "<script>" not in page and "<img" not in page
    assert "&lt;script&gt;alert(&#34;x&#34;)&lt;/script&gt;" in page
    assert "&#34;&gt;&lt;img src=x onerror=alert(1)&gt;" in page


def test_sandbox_blocks_attribute_tricks_in_values() -> None:
    evil = content().model_copy(deep=True)
    evil.hero.subhead = "{{ ''.__class__.__mro__ }}"
    page = html(render(evil))
    # values are data, never re-evaluated as template code
    assert "{{ &#39;&#39;.__class__.__mro__ }}" in page
    assert not re.search(r"<class '", page)


@pytest.mark.parametrize("business_name", ["Chai & Co", "O'Brien Foods"])
def test_business_name_is_escaped(business_name: str) -> None:
    page = html(render(business_name=business_name))
    escaped = business_name.replace("&", "&amp;").replace("'", "&#39;")
    assert f'<p class="brand">{escaped}</p>' in page
