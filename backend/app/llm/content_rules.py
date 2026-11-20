"""Deterministic safety rules for model-written website text (FR-SITE-1, FR-SITE-6, FR-SITE-8).

Every text value the model writes for a site is plain text. These checks reject, rather than
repair, anything that could become markup, a link or a fabricated proof claim. They run inside
the `WebsiteContent` schema, so a violation triggers run_structured's repair retries.
Messages name the field path and the rule, never the offending value.

The Jinja renderer autoescapes as a second layer; these rules do not rely on it.
"""

import re
import unicodedata
from collections.abc import Iterable

# Anything that can open a tag or an entity-based tag. `&` alone is fine (it gets escaped).
_MARKUP = re.compile(r"[<>]|&#|&lt|&gt|\{\{|\{%", re.IGNORECASE)

_LINK = re.compile(
    r"(?:[a-z][a-z0-9+.-]*://)"  # any scheme://
    r"|\bwww\."
    r"|\b(?:javascript|data|vbscript|mailto|tel|file):"
    r"|\b[a-z0-9-]+\.(?:com|in|co|net|org|io|biz|app|xyz|info|ai|me|shop|store|site|online)\b"
    r"|[^\s@]+@[^\s@]+\.[a-z]{2,}",  # email address
    re.IGNORECASE,
)

# Claims of social proof or results that NOVENTRA cannot have for an unlaunched business.
# Phrases, not isolated words: "we review every order" or "100% vegetarian" are fine, while
# "great reviews", "rated 4.9", "five-star", "saves 30%" or "trusted by" are not.
_N = r"(?:\d+(?:[.,]\d+)?|one|two|three|four|five|ten|hundreds|thousands|many|countless)"
_PROOF_CLAIMS = re.compile(
    # ratings and stars
    rf"\brated\s+(?:\d|{_N}\b|by\b|as\b|highly|top|the\s+best)"
    r"|\b(?:highly|top|best|five|5|4|four)[-\s]?(?:rated|star|stars)\b"
    r"|\b\d(?:\.\d)?\s*(?:stars?|star\s+rating)\b"
    r"|\b\d(?:\.\d)?\s*/\s*(?:5|10)\b"
    r"|\b(?:average|customer|star|user|app\s+store)\s+ratings?\b"
    r"|\bratings?\s+of\b|★|⭐"
    # reviews and testimonials
    rf"|\b{_N}\s+(?:of\s+)?(?:positive\s+|good\s+|great\s+)?(?:reviews|testimonials|ratings)\b"
    r"|\b(?:great|good|positive|glowing|rave|excellent|amazing|stellar|top|real|verified|"
    r"customer|client|our|5-star|five-star)\s+(?:reviews|testimonials)\b"
    r"|\b(?:reviews|testimonials)\s+(?:from|say|show|prove|speak)\b"
    r"|\b(?:read|see|check)\s+(?:our\s+|the\s+)?(?:reviews|testimonials)\b"
    r"|\breviewed\s+by\b|\bfeatured\s+(?:in|on|by)\b"
    # endorsements and awards
    r"|\b(?:trusted|loved|backed|endorsed|recommended)\s+by\b"
    rf"|\b(?:used|chosen|preferred)\s+by\s+(?:{_N}\b|over\b|more\s+than\b|leading\b|top\b)"
    r"|\bas\s+seen\s+(?:in|on)\b|\baward[-\s]?winning\b"
    r"|\bwon\s+(?:an?\s+|the\s+|several\s+|many\s+)?awards?\b|\bawards?\s+(?:from|for)\b"
    r"|#\s?1\b|\bnumber\s+one\b|\bbest[-\s]?selling\b|\bmarket\s+leader\b"
    r"|\b(?:customers|clients|users|parents|students)\s+(?:say|love|agree|rave)\b"
    # result percentages (a plain "100% vegetarian" is a product fact, not a claim)
    r"|\b(?:save|saves|saving|cut|cuts|reduce|reduces|boost|boosts|increase|increases|"
    r"improve|improves|grow|grows|up\s+to|over|more\s+than)\s+\d+(?:[.,]\d+)?\s*(?:%|percent\b)"
    r"|\d+(?:[.,]\d+)?\s*(?:%|percent\b)\s*(?:of\s+(?:our\s+)?(?:customers|users|clients)"
    r"|satisfaction|satisfied|happier|faster|cheaper|more|less|better|growth|increase"
    r"|reduction|savings|success|retention|repeat)",
    re.IGNORECASE,
)

# "No reviews yet", "not rated yet", "without testimonials": an honest absence, not a claim.
_NEGATION_BEFORE = re.compile(
    r"\b(?:no|not|zero|without|yet\s+to\s+(?:have|get|receive|earn)|don'?t\s+have"
    r"|do\s+not\s+have|haven'?t|have\s+not)\s+(?:any\s+)?"
    r"(?:customer\s+|client\s+|real\s+|online\s+|public\s+|verified\s+)?$",
    re.IGNORECASE,
)

# A count of people or businesses served ("500+ customers", "2k users", "10 lakh families").
_COUNT_CLAIM = re.compile(
    r"\b\d[\d,.]*\s*(?:\+|k\b|lakh\b|lakhs\b|crore\b|million\b|thousand\b)?\s*"
    r"(?:happy\s+|satisfied\s+|active\s+)?"
    r"(?:customers|clients|users|members|families|students|businesses|shops|subscribers"
    r"|downloads|orders\s+delivered|deliveries\s+made)\b",
    re.IGNORECASE,
)

_FORBIDDEN_CATEGORIES = {"Cc", "Cf", "Co", "Cs"}  # control, format (bidi/zero-width), private


def _bad_char(text: str) -> bool:
    return any(unicodedata.category(ch) in _FORBIDDEN_CATEGORIES for ch in text)


def _has_proof_claim(text: str) -> bool:
    for m in _PROOF_CLAIMS.finditer(text):
        before = text[max(0, m.start() - 30) : m.start()]
        if not _NEGATION_BEFORE.search(before):
            return True
    return False


def text_problems(text: str, *, proof_claims: bool = True, counts: bool = True) -> list[str]:
    """Rule names violated by one plain-text value (empty list = clean)."""
    problems = []
    if _bad_char(text):
        problems.append("contains control or invisible formatting characters")
    if _MARKUP.search(text):
        problems.append("contains markup characters; write plain text only")
    if _LINK.search(text):
        problems.append("contains a link, domain or email address")
    if proof_claims and _has_proof_claim(text):
        problems.append(
            "contains a rating, review, percentage or endorsement claim; never invent proof"
        )
    if counts and _COUNT_CLAIM.search(text):
        problems.append("claims a number of customers or users; never invent proof")
    return problems


def check_fields(
    fields: Iterable[tuple[str, str, bool, bool]],
) -> list[str]:
    """Check (path, text, proof_claims, counts) tuples; return 'path: rule' messages."""
    return [
        f"{path}: {problem}"
        for path, text, proof, counts in fields
        for problem in text_problems(text, proof_claims=proof, counts=counts)
    ]
