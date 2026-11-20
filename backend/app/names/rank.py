"""Deterministic slug ranking and the candidate pipeline (FR-BR-1, FR-BR-3). Pure functions.

Only the slug is scored. The model's rationale is display text and never affects a score.
All weights and thresholds come from app/names/config.py; the algorithm is in docs/names.md.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from app.names import config as c
from app.names.normalize import RawCandidate
from app.names.slug import slugify

_WORD = re.compile(r"[a-z]+")


def _clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def length_factor(slug: str) -> float:
    n = len(slug)
    if n < c.LENGTH_SWEET_MIN:
        return _clamp(1 - c.LENGTH_SHORT_PENALTY * (c.LENGTH_SWEET_MIN - n))
    if n > c.LENGTH_SWEET_MAX:
        return _clamp(1 - (n - c.LENGTH_SWEET_MAX) / c.LENGTH_LONG_SPAN)
    return 1.0


def pronounceability_factor(slug: str) -> float:
    letters = "".join(ch for ch in slug if ch.isalpha())
    if not letters:
        return 0.0
    ratio = sum(ch in c.VOWELS for ch in letters) / len(letters)
    if ratio < c.VOWEL_RATIO_MIN:
        vowel = _clamp(1 - c.VOWEL_RATIO_SLOPE * (c.VOWEL_RATIO_MIN - ratio))
    elif ratio > c.VOWEL_RATIO_MAX:
        vowel = _clamp(1 - c.VOWEL_RATIO_SLOPE * (ratio - c.VOWEL_RATIO_MAX))
    else:
        vowel = 1.0
    vowels = "".join(sorted(c.VOWELS))
    # Each letter beyond the allowed run length costs RUN_PENALTY ("str" = 1, "ngth" = 2).
    excess = sum(
        len(m.group()) - (c.CONSONANT_RUN - 1)
        for m in re.finditer(f"[^{vowels}]{{{c.CONSONANT_RUN},}}", letters)
    )
    excess += sum(
        len(m.group()) - (c.REPEAT_RUN - 1)
        for m in re.finditer(f"(.)\\1{{{c.REPEAT_RUN - 1},}}", letters)
    )
    structure = _clamp(1 - c.RUN_PENALTY * excess)
    return (vowel + structure) / 2


def typeability_factor(slug: str) -> float:
    hits = sum(ch in c.AMBIGUOUS_CHARS for ch in slug)
    hits += sum(slug.count(seq) for seq in c.AMBIGUOUS_SEQUENCES)
    return _clamp(1 - c.AMBIGUITY_PENALTY * hits)


def idea_keywords(text: str) -> frozenset[str]:
    """Lowercase ASCII words of KEYWORD_MIN_LEN+ letters from the idea, minus stopwords."""
    words = _WORD.findall((text or "").lower())
    return frozenset(w for w in words if len(w) >= c.KEYWORD_MIN_LEN and w not in c.STOPWORDS)


def keyword_factor(slug: str, keywords: frozenset[str]) -> float:
    if any(k in slug for k in keywords):
        return c.KEYWORD_FULL
    if any(len(k) > c.KEYWORD_STEM_LEN and k[: c.KEYWORD_STEM_LEN] in slug for k in keywords):
        return c.KEYWORD_STEM
    return 0.0


def score_slug(slug: str, keywords: frozenset[str]) -> tuple[float, dict[str, dict[str, float]]]:
    """Return (total 0-100, per-factor {value 0-1, weight, points})."""
    values = {
        "length": length_factor(slug),
        "pronounceability": pronounceability_factor(slug),
        "no_digits": 0.0 if any(ch.isdigit() for ch in slug) else 1.0,
        "no_hyphens": 0.0 if "-" in slug else 1.0,
        "typeability": typeability_factor(slug),
        "keyword_overlap": keyword_factor(slug, keywords),
    }
    factors: dict[str, dict[str, float]] = {}
    total = 0.0
    for key, weight in c.WEIGHTS.items():
        value = round(values[key], c.FACTOR_PRECISION)
        points = round(weight * value, c.FACTOR_PRECISION)
        factors[key] = {"value": value, "weight": weight, "points": points}
        total += points
    return round(total, c.TOTAL_PRECISION), factors


@dataclass
class NameRecord:
    name: str
    rationale: str
    slug: str
    valid: bool
    invalid_reason: str | None
    rank: int | None = None
    score_total: float | None = None
    score_factors: dict = field(default_factory=dict)


def build_candidates(
    raw: Iterable[RawCandidate], *, keywords: frozenset[str], apex: str | None
) -> list[NameRecord]:
    """Slug, validate, de-duplicate slugs, score and rank already-normalized candidates.

    Invalid candidates are kept with their reason code (unranked, unscored). A valid
    candidate whose slug equals an earlier valid candidate's slug becomes `duplicate_slug`.
    Valid candidates are ranked 1..N by (-score_total, slug), so ties break alphabetically.
    Output order: ranked candidates first, then invalid ones in input order.
    """
    records: list[NameRecord] = []
    claimed: set[str] = set()
    for item in raw:
        result = slugify(item.name, apex=apex)
        reason = result.reason
        if reason is None and result.slug in claimed:
            reason = c.DUPLICATE_SLUG
        if reason is None:
            claimed.add(result.slug)
        records.append(NameRecord(item.name, item.rationale, result.slug, reason is None, reason))

    valid = [r for r in records if r.valid]
    for r in valid:
        r.score_total, r.score_factors = score_slug(r.slug, keywords)
    valid.sort(key=lambda r: (-(r.score_total or 0.0), r.slug))
    for i, r in enumerate(valid, start=1):
        r.rank = i
    return valid + [r for r in records if not r.valid]
