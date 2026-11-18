"""Deterministic stub stages for M1. Replaced stage by stage in M2+ (no LLM calls here).

Kept separate from runner/status logic: nothing here touches the database or the
transition rules. The stub verdict is a pure function of the idea text.
"""

from app.models import Business, BusinessStatus
from app.pipeline.runner import Handler, StageResult

S = BusinessStatus

# The vague-idea fixture (fixtures/ideas/weak.json) contains this phrase.
STUB_REJECT_PHRASE = "everything for everyone"


def stub_verdict(idea_text: str) -> bool:
    """True means 'viable'. Deterministic stand-in for the real pre-mortem verdict."""
    return STUB_REJECT_PHRASE not in idea_text.lower()


def _step(next_status: BusinessStatus, reason: str, version_ref: str | None = None) -> Handler:
    def handler(_session: object, _business: Business) -> StageResult:
        return StageResult(next_status, f"[stub] {reason}", version_ref)

    return handler


def _premortem(_session: object, business: Business) -> StageResult:
    if stub_verdict(business.idea_text):
        return StageResult(S.GATED, "[stub] pre-mortem verdict: viable")
    return StageResult(S.REJECTED, "[stub] pre-mortem verdict: rejected as too vague")


def default_stages() -> dict[BusinessStatus, Handler]:
    return {
        S.INTAKE: _step(S.PREMORTEM, "intake accepted"),
        S.PREMORTEM: _premortem,
        S.GATED: _step(S.DOMAIN_PENDING, "gate passed"),
        S.DOMAIN_PENDING: _step(S.DEPLOYING, "domain selected"),
        S.DEPLOYING: _step(S.LIVE, "site deployed", "v1"),
        S.LIVE: _step(S.TESTING, "site live"),
        S.TESTING: _step(S.IMPROVING, "persona test complete"),
        S.IMPROVING: _step(S.DONE, "improvement cycle complete"),
    }
