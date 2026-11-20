"""Name-candidate text normalization and de-duplication (FR-BR-1). Pure functions."""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

_WS = re.compile(r"\s+")


@dataclass(frozen=True)
class RawCandidate:
    name: str
    rationale: str


def clean_text(text: str) -> str:
    """NFKC, trim, and collapse every whitespace run to one space."""
    return _WS.sub(" ", unicodedata.normalize("NFKC", text or "")).strip()


def normalize_candidates(items: Iterable[tuple[str, str]]) -> list[RawCandidate]:
    """Clean names and rationales, drop empty names, keep the first of case-insensitive duplicates.

    Order is preserved so later steps (duplicate-slug detection) are deterministic.
    """
    seen: set[str] = set()
    out: list[RawCandidate] = []
    for name, rationale in items:
        clean = clean_text(name)
        if not clean:
            continue
        key = clean.casefold()
        if key in seen:
            continue
        seen.add(key)
        out.append(RawCandidate(clean, clean_text(rationale)))
    return out
