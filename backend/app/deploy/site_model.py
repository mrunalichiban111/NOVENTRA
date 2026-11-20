"""Render model for a generated site (FR-SITE-1, FR-SITE-2, FR-SITE-8, FR-PR-4). Pure functions.

Combines validated model content with facts that only Python controls: the business name and
host (from the database), the founder's price, and operator-configured links (waitlist form
target and abuse-report address). The model's `price_inr` is always ignored: the page shows the
founder's own `price_guess`, or says pricing will be shared before launch. All arithmetic and
formatting of money happens here.
"""

import hashlib
import json
import re
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from urllib.parse import urlsplit

from app.llm.content_rules import text_problems
from app.llm.schemas import WebsiteContent

TEMPLATE_VERSION = "site-1"
NO_PRICE_NOTE = "Pricing will be shared before launch."
PRICE_NOTE = "Planned launch price, to be confirmed before launch."

# Shape only; Block 3's hosts.validate_host enforces <label>.<BASE_DOMAIN> before publishing.
_HOST = re.compile(
    r"^(?=.{1,253}$)[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?(?:\.[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?)+$"
)
_EMAIL = re.compile(r"^[A-Za-z0-9._%+-]{1,64}@[A-Za-z0-9-]{1,63}(?:\.[A-Za-z0-9-]{1,63})+$")


class SiteConfigError(ValueError):
    """Operator configuration for generated sites is invalid."""


@dataclass(frozen=True)
class SiteLinks:
    """Operator-controlled link targets. None means the element is omitted."""

    waitlist_action: str | None = None
    abuse_email: str | None = None

    @property
    def form_origin(self) -> str | None:
        if not self.waitlist_action:
            return None
        parts = urlsplit(self.waitlist_action)
        return f"{parts.scheme}://{parts.netloc}"


def site_links(waitlist_post_url: str, abuse_report_email: str) -> SiteLinks:
    """Validate operator config (WAITLIST_POST_URL, ABUSE_REPORT_EMAIL). Empty = omitted."""
    action = waitlist_post_url.strip() or None
    if action is not None:
        parts = urlsplit(action)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or any(ch in action for ch in "\"'<> \\")
        ):
            raise SiteConfigError("WAITLIST_POST_URL must be a plain https URL")
    email = abuse_report_email.strip() or None
    if email is not None and not _EMAIL.fullmatch(email):
        raise SiteConfigError("ABUSE_REPORT_EMAIL is not a valid email address")
    return SiteLinks(waitlist_action=action, abuse_email=email)


def format_inr(amount: Decimal) -> str:
    """Rupees with Indian digit grouping: 125000 -> '₹1,25,000'; 99.5 -> '₹99.50'."""
    if amount < 0:
        raise ValueError("price cannot be negative")
    whole, _, paise = f"{amount.quantize(Decimal('0.01')):f}".partition(".")
    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join([*groups, tail])
    return f"₹{whole}" + ("" if paise == "00" else f".{paise}")


def pricing_view(content: WebsiteContent, price_guess: Decimal | None) -> dict[str, Any]:
    """Founder-only pricing (approved M4B decision). The model's price_inr is never shown."""
    if price_guess is None:
        return {"plan": None, "note": NO_PRICE_NOTE}
    plan = content.pricing[0]
    price = "Free" if price_guess == 0 else format_inr(price_guess)
    return {
        "plan": {"name": plan.name, "price": price, "bullets": list(plan.bullets)},
        "note": PRICE_NOTE,
    }


def content_hash(content: WebsiteContent) -> str:
    """sha256 of canonical content JSON plus the template version (FR-SITE-3 idempotency key)."""
    canonical = json.dumps(
        content.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(f"{TEMPLATE_VERSION}\n{canonical}".encode()).hexdigest()


def _check_name(business_name: str) -> str:
    name = business_name.strip()
    if not name or len(name) > 63:
        raise ValueError("business name must be 1-63 characters")
    if text_problems(name, proof_claims=False, counts=False):
        raise ValueError("business name must be plain text without links or markup")
    return name


def build_site_model(
    content: WebsiteContent,
    *,
    business_name: str,
    host: str,
    price_guess: Decimal | None,
    links: SiteLinks,
) -> dict[str, Any]:
    """Plain dict/list/str context for the sandboxed template (no objects with methods)."""
    data = content.model_dump(mode="json")
    data.pop("pricing")
    if not _HOST.fullmatch(host):
        raise ValueError("host must be a lowercase DNS name")
    form_src = links.form_origin or "'none'"
    csp = (
        "default-src 'none'; style-src 'self'; img-src 'self' data:; "
        f"form-action {form_src}; base-uri 'none'"
    )
    return {
        "business_name": _check_name(business_name),
        "host": host,
        "lang": "en",
        "csp": csp,
        "content": data,
        "pricing": pricing_view(content, price_guess),
        "waitlist_action": links.waitlist_action,
        "abuse_email": links.abuse_email,
        "template_version": TEMPLATE_VERSION,
    }
