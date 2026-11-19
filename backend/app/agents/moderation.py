"""Idea moderation (FR-PR-3): refuse illegal, deceptive or harmful businesses and log it.

Deterministic and conservative on purpose: a small set of patterns for clearly criminal or
deceptive INTENT (scams, phishing, impersonation, counterfeits, fraud, trafficking). It runs
before any model call, so a refused idea costs nothing and cannot be talked around by prompt
injection. It is NOT a commercial-viability filter: a weak, saturated or unrealistic idea
passes moderation and receives a Rethink verdict from the scoring gate instead.

Known limits (documented, not hidden): keyword rules miss obfuscated or novel phrasing and may
occasionally flag a legitimate business that merely mentions these words. Defensive phrasing
("anti-phishing training", "scam detection") is exempt. A model refusal during the pre-mortem
is treated the same way (see handlers). Review and widen the list before a public launch.
"""

import re
from dataclasses import dataclass

_FLAGS = re.IGNORECASE


def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, _FLAGS)


# category -> patterns. Each pattern should describe doing the harmful thing.
_RULES: dict[str, list[re.Pattern[str]]] = {
    "phishing or credential theft": [
        _p(r"\bphishing\b"),
        _p(
            r"\b(steal|harvest|collect|capture)\w*\b[^.]{0,40}\b(passwords?|card numbers?|otps?|login (details|credentials)|bank details)"
        ),
        _p(r"\b(copies|clones?|fake|spoof\w*)\b[^.]{0,30}\b(login|bank|website|page)\b"),
    ],
    "scam or fraud": [
        _p(r"\bponzi\b|\bpyramid scheme\b"),
        _p(
            r"\b(scam|defraud|swindle|con)\b[^.]{0,30}\b(people|customers|victims|elderly|investors|users)\b"
        ),
        _p(r"\bfake (investment|lottery|job offers?|charity|giveaway)s?\b"),
        _p(r"\bguaranteed (returns?|profits?)\b[^.]{0,40}\b(no risk|risk[- ]free|double)"),
        _p(r"\bmoney laundering\b|\blaunder\w* money\b"),
    ],
    "impersonation": [
        _p(r"\b(impersonat\w+|pretend\w* to be|pose as|posing as)\b"),
        _p(
            r"\b(look|looks|appear|appears)\s+(like )?(they )?(come|comes|came)? ?from (major |a |the )?(bank|government|police|court|tax)"
        ),
    ],
    "counterfeit or forged goods": [
        _p(r"\b(counterfeit|forged|forge)\b"),
        _p(
            r"\bfake (id|ids|passports?|certificates?|degrees?|documents?|currency|notes|reviews?)\b"
        ),
    ],
    "illegal goods or services": [
        _p(
            r"\b(sell|selling|supply|supplying|traffick\w*|smuggl\w*)\b[^.]{0,30}\b(illegal drugs?|cocaine|heroin|meth\w*|weapons?|firearms?|explosives?|stolen (goods|data|cards?))"
        ),
        _p(r"\bsell\w*\b[^.]{0,40}\b(collected|stolen|leaked) (details|data|passwords|cards)"),
        _p(r"\b(hire|hiring) (a )?(hit ?man|assassin)\b|\bhuman trafficking\b"),
    ],
    "harm to people": [
        _p(r"\b(stalk\w*|doxx\w*|blackmail\w*|extort\w*)\b"),
        _p(r"\bspy on\b[^.]{0,30}\b(partner|spouse|ex|girlfriend|boyfriend|wife|husband)\b"),
    ],
}

# Phrases that signal a defensive business; matched text is removed before the rules run.
_DEFENSIVE = _p(
    r"\banti[- ]?(phishing|scam|fraud|counterfeit)\b"
    r"|\b(phishing|scam|fraud|counterfeit)\s+(detection|prevention|awareness|training|protection)\b"
    r"|\b(detect|prevent|report|block|stop|protect (people|users|customers) from)\w*\s+(phishing|scams?|fraud|counterfeits?)\b"
)


@dataclass(frozen=True)
class ModerationResult:
    allowed: bool
    category: str | None = None
    reason: str | None = None


def check_idea(idea_text: str) -> ModerationResult:
    """Return allowed=False with a clear reason for clearly harmful ideas, else allowed=True."""
    text = _DEFENSIVE.sub(" ", idea_text)
    for category, patterns in _RULES.items():
        for pattern in patterns:
            if pattern.search(text):
                return ModerationResult(
                    False,
                    category,
                    f"Rejected for safety ({category}): NOVENTRA does not assess illegal, "
                    "deceptive or harmful business ideas. This is a safety refusal, not a "
                    "commercial verdict.",
                )
    return ModerationResult(True)
