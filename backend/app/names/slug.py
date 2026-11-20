"""Slug derivation and subdomain-label validation (FR-BR-5). Pure functions, no database.

A slug is derived deterministically and never structurally repaired: accents are folded
(é -> e), letters are lowercased, apostrophes are dropped and each whitespace run becomes one
hyphen. Hyphens already in the name are kept as they are, so "--x" stays "--x" and fails with
`edge_hyphen` instead of silently becoming "x". Anything else outside [a-z0-9-] fails with
`unsupported_char` (no IDN, no transliteration of non-Latin scripts).
"""

import re
import unicodedata
from dataclasses import dataclass

from app.names import config as c

_WS = re.compile(r"\s+")
_ALLOWED = re.compile(r"[a-z0-9-]*")


@dataclass(frozen=True)
class SlugResult:
    slug: str
    reason: str | None  # None means valid

    @property
    def valid(self) -> bool:
        return self.reason is None


def derive_slug(name: str) -> str:
    """Deterministic slug text for a name. May be invalid; see `validate_slug`."""
    folded = unicodedata.normalize("NFKD", name)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = "".join(ch for ch in folded.lower() if ch not in c.DROPPED_CHARS)
    return _WS.sub("-", folded.strip())


def apex_label(base_domain: str | None) -> str | None:
    """First label of the configured apex domain ("noventra.in" -> "noventra")."""
    if not base_domain:
        return None
    label = base_domain.strip().strip(".").split(".")[0].lower()
    return label or None


def validate_slug(
    slug: str,
    *,
    apex: str | None = None,
    reserved: frozenset[str] = c.RESERVED_SLUGS,
) -> str | None:
    """Return the first failing reason code, or None if `slug` is a claimable label."""
    if not _ALLOWED.fullmatch(slug):
        return c.UNSUPPORTED_CHAR
    if slug.startswith(c.PUNYCODE_PREFIX):
        return c.PUNYCODE
    if slug.startswith("-") or slug.endswith("-"):
        return c.EDGE_HYPHEN
    if "--" in slug:
        return c.DOUBLE_HYPHEN
    if len(slug) < c.SLUG_MIN_LEN:
        return c.TOO_SHORT
    if len(slug) > c.SLUG_MAX_LEN:
        return c.TOO_LONG
    if slug in reserved:
        return c.RESERVED
    if apex is not None and slug == apex:
        return c.APEX_LABEL
    return None


def slugify(name: str, *, apex: str | None = None) -> SlugResult:
    slug = derive_slug(name)
    return SlugResult(slug, validate_slug(slug, apex=apex))
