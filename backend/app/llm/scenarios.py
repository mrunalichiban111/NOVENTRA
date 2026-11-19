"""Controlled fixture scenarios for MOCK_LLM mode (never used for real calls).

A scenario is an id such as "good" that selects fixtures/llm/<id>/. Ids are matched against
a closed pattern and must name an existing directory; idea text is NEVER used as a path.
`scenario_for_idea` maps a *known sample idea* to its scenario by hashing the normalised
text and looking it up in fixtures/ideas/*.json (entries carrying a "scenario" field).
Unknown ideas get None, i.e. the default fixtures.
"""

import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path

from app import config
from app.llm.base import ConfigError

_ID = re.compile(r"^[a-z][a-z0-9_]{0,39}$")


def check_scenario_id(scenario: str) -> str:
    if not isinstance(scenario, str) or not _ID.fullmatch(scenario):
        raise ConfigError(f"invalid fixture scenario id: {scenario!r}")
    if scenario not in known_scenarios():
        raise ConfigError(f"unknown fixture scenario: {scenario!r}")
    return scenario


def _fixtures_root() -> Path:
    return Path(config.FIXTURES_DIR)


def known_scenarios() -> frozenset[str]:
    llm = _fixtures_root() / "llm"
    if not llm.is_dir():
        return frozenset()
    return frozenset(p.name for p in llm.iterdir() if p.is_dir() and _ID.fullmatch(p.name))


def _digest(text: str) -> str:
    normalised = " ".join(text.lower().split())
    return hashlib.sha256(normalised.encode("utf-8")).hexdigest()


@lru_cache(maxsize=8)
def _index(ideas_dir: str) -> dict[str, str]:
    index: dict[str, str] = {}
    for path in sorted(Path(ideas_dir).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        scenario, idea = data.get("scenario"), data.get("idea")
        if isinstance(scenario, str) and isinstance(idea, str) and _ID.fullmatch(scenario):
            index[_digest(idea)] = scenario
    return index


def scenario_for_idea(idea_text: str) -> str | None:
    """Scenario id for a known sample idea, else None. Only meaningful in mock mode."""
    return _index(str(_fixtures_root() / "ideas")).get(_digest(idea_text))
