"""M3 pipeline: INTAKE -> PREMORTEM -> GATED with moderation, resume and retry (FR-PM, FR-PR-3)."""

import pytest

from app.llm.base import RefusalError, RunIdRequired, StructuredOutputError, TransportError
from app.pipeline import repository, status
from app.pipeline.handlers import m3_stages
from app.pipeline.status import Actor
from tests.m3_helpers import (
    SpyLLM,
    assumptions,
    audit,
    budget,
    get,
    idea,
    new_business,
    run,
    scorecards,
)


def failure(msg: str = "boom") -> StructuredOutputError:
    return StructuredOutputError(msg, task="t", attempts=3, last_error=msg)


# --- the three scenario ideas ---------------------------------------------------------


@pytest.mark.parametrize(
    "name,verdict",
    [("good", "go"), ("rethink", "rethink"), ("homemade_food", "go_with_changes")],
)
def test_scenarios_reach_gated_with_their_verdict(session_factory, name, verdict) -> None:
    bid = new_business(session_factory, idea(name), budget=budget(name))
    assert run(session_factory, bid).value == "GATED"

    b = get(session_factory, bid)
    assert b.status == "GATED" and b.rejection_reason is None
    assert b.target_customer and b.premortem_json["source"] == "llm"
    cards = scorecards(session_factory, bid)
    assert len(cards) == 1
    c = cards[0]
    assert (c.version, c.stage, c.verdict, c.demand) == (1, "pre", verdict, None)
    assert c.details_json["explanation"]  # explanation lines always present
    assert isinstance(c.details_json["seed"], int)
    assert c.details_json["monte_carlo"]["simulated"] is True
    assert c.details_json["monte_carlo"]["n"] == 2000


def test_rethink_idea_stops_in_gated_and_is_not_a_safety_rejection(session_factory) -> None:
    bid = new_business(session_factory, idea("rethink"), budget=budget("rethink"))
    run(session_factory, bid)
    b = get(session_factory, bid)
    assert b.status == "GATED"  # Rethink halts here; it is not REJECTED
    assert b.rejection_reason is None
    assert scorecards(session_factory, bid)[0].verdict == "rethink"
    assert float(scorecards(session_factory, bid)[0].economics) == 0.0


def test_premortem_labels_are_added_by_python(session_factory) -> None:
    bid = new_business(session_factory, idea("good"))
    run(session_factory, bid)
    pm = get(session_factory, bid).premortem_json
    assert len(pm["competitors"]) == 3
    assert all(c["verified"] is False and c["label"] == "unverified" for c in pm["competitors"])
    assert "not legal advice" in pm["disclaimer"].lower()
    assert pm["compliance_flags"]
    assert all(
        f["verify"] is True and "official" in f["verification_note"].lower()
        for f in pm["compliance_flags"]
    )
    assert len(pm["failure_reasons"]) == 3
    assert set(pm["risk_severities"]) == {
        "market_saturation", "operational_complexity", "compliance", "capital_need", "founder_load",
    }  # fmt: skip
    assert pm["suggested_pivots"]


def test_assumptions_are_persisted_with_source_llm(session_factory) -> None:
    bid = new_business(session_factory, idea("good"))
    run(session_factory, bid)
    rows = assumptions(session_factory, bid)
    assert len(rows) == 6
    assert {r.source for r in rows} == {"llm"}
    assert all(r.rationale for r in rows)


def test_pipeline_stops_at_gated_and_worker_never_claims_it(session_factory) -> None:
    bid = new_business(session_factory, idea("good"))
    run(session_factory, bid)
    spy = SpyLLM()
    assert run(session_factory, bid, spy).value == "GATED"  # re-running changes nothing
    assert spy.calls == []
    from app.pipeline.status import RETRYABLE_STAGES

    halted = RETRYABLE_STAGES - m3_stages().keys()
    assert {s.value for s in halted} >= {"GATED"}
    with session_factory() as s:
        assert repository.claim_next(s, "w2", 60, exclude=halted) is None


def test_every_transition_is_audited(session_factory) -> None:
    bid = new_business(session_factory, idea("good"))
    run(session_factory, bid)
    rows = audit(session_factory, bid)
    assert [(r.from_status, r.to_status) for r in rows] == [
        (None, "INTAKE"), ("INTAKE", "PREMORTEM"), ("PREMORTEM", "GATED"),
    ]  # fmt: skip
    gated = rows[-1]
    assert gated.actor == "worker:w1" and gated.version_ref == "pre-v1"
    assert "go" in gated.reason.lower() and "E " in gated.reason


# --- moderation (FR-PR-3) ---------------------------------------------------------------


def test_scam_idea_is_rejected_before_any_model_call(session_factory) -> None:
    spy = SpyLLM()
    bid = new_business(session_factory, idea("scam"))
    assert run(session_factory, bid, spy).value == "REJECTED"
    assert spy.calls == []  # deterministic check: no spend
    b = get(session_factory, bid)
    assert b.status == "REJECTED"
    assert (
        "safety" in b.rejection_reason.lower() and "not a commercial verdict" in b.rejection_reason
    )
    assert b.premortem_json is None and not assumptions(session_factory, bid)
    assert not scorecards(session_factory, bid)
    last = audit(session_factory, bid)[-1]
    assert (last.from_status, last.to_status, last.action) == (
        "PREMORTEM", "REJECTED", "moderation.rejected",
    )  # fmt: skip
    assert last.reason == b.rejection_reason


def test_model_refusal_is_a_safety_rejection(session_factory) -> None:
    spy = SpyLLM({"premortem": [RefusalError("declined")]})
    bid = new_business(session_factory, idea("good"))
    assert run(session_factory, bid, spy).value == "REJECTED"
    b = get(session_factory, bid)
    assert "declined" in b.rejection_reason
    assert audit(session_factory, bid)[-1].action == "moderation.rejected"


def test_commercially_weak_idea_is_not_rejected(session_factory) -> None:
    # "Everything for everyone" is vague, not unsafe: it gets a scorecard, never REJECTED.
    bid = new_business(session_factory, idea("weak"))
    assert run(session_factory, bid).value == "GATED"
    assert get(session_factory, bid).rejection_reason is None


# --- failure, retry, resume -----------------------------------------------------------


def test_failed_extraction_keeps_premortem_and_resumes_without_repeating_it(
    session_factory,
) -> None:
    spy = SpyLLM({"assumption_extraction": [failure("bad json")]})
    bid = new_business(session_factory, idea("good"))
    assert run(session_factory, bid, spy).value == "FAILED"

    b = get(session_factory, bid)
    assert b.status == "FAILED" and b.failed_stage == "PREMORTEM"
    assert "StructuredOutputError" in b.last_error and "bad json" in b.last_error
    assert b.premortem_json is not None  # paid result kept
    assert not assumptions(session_factory, bid) and not scorecards(session_factory, bid)

    with session_factory() as s:
        status.retry(s, bid, actor=Actor.user(), reason="retry")
        s.commit()
    assert run(session_factory, bid, spy).value == "GATED"
    assert spy.tasks() == ["premortem", "assumption_extraction", "assumption_extraction"]
    assert len(scorecards(session_factory, bid)) == 1


def test_failed_premortem_is_retryable_and_audited(session_factory) -> None:
    spy = SpyLLM({"premortem": [TransportError("down", retryable=False)]})
    bid = new_business(session_factory, idea("good"))
    assert run(session_factory, bid, spy).value == "FAILED"
    with session_factory() as s:
        status.retry(s, bid, actor=Actor.user())
        s.commit()
    assert run(session_factory, bid, spy).value == "GATED"
    assert spy.tasks() == ["premortem", "premortem", "assumption_extraction"]
    actions = [r.action for r in audit(session_factory, bid)]
    assert "pipeline.failed" in actions and "pipeline.retry" in actions
    assert actions[-1] == "stage.premortem"


def test_completed_stages_never_rerun_after_gated(session_factory) -> None:
    spy = SpyLLM()
    bid = new_business(session_factory, idea("good"))
    run(session_factory, bid, spy)
    run(session_factory, bid, spy)
    assert spy.tasks() == ["premortem", "assumption_extraction"]


def test_retry_limit_is_still_enforced(session_factory) -> None:
    spy = SpyLLM({"premortem": [failure() for _ in range(5)]})
    bid = new_business(session_factory, idea("good"))
    for _ in range(3):
        run(session_factory, bid, spy)
        with session_factory() as s:
            try:
                status.retry(s, bid, actor=Actor.user())
                s.commit()
            except status.RetryLimitExceeded:
                s.rollback()
    assert get(session_factory, bid).status == "FAILED"
    with session_factory() as s, pytest.raises(status.RetryLimitExceeded):
        status.retry(s, bid, actor=Actor.user())


# --- run id and budget guard propagation ---------------------------------------------------


def test_every_llm_call_receives_the_runner_run_id_and_business_id(session_factory) -> None:
    spy = SpyLLM()
    bid = new_business(session_factory, idea("good"))
    run(session_factory, bid, spy)
    assert len(spy.calls) == 2
    ids = {c["run_id"] for c in spy.calls}
    assert len(ids) == 1
    rid = ids.pop()
    assert rid and rid != "-"
    assert all(c["context_run_id"] == rid and c["business_id"] == bid for c in spy.calls)


def test_idea_text_is_only_passed_as_prompt_data_and_scenario_is_a_controlled_id(
    session_factory,
) -> None:
    spy = SpyLLM()
    bid = new_business(session_factory, idea("good"))
    run(session_factory, bid, spy)
    assert all(idea("good") in c["prompt"] for c in spy.calls)
    assert {c["scenario"] for c in spy.calls} == {"good"}


def test_real_mode_calls_reach_the_budget_guard_with_the_run_id(
    session_factory, monkeypatch
) -> None:
    """With MOCK_LLM off, run_structured builds LLMGuard(run_id=...) and calls before/after."""
    from app import config
    from app.llm import budget
    from app.llm.mock import MockProvider
    from app.llm.structured import run_structured

    events: list[tuple] = []

    class FakeGuard:
        def __init__(self, *, business_id, run_id, provider_name="claude", **_):
            self.business_id, self.run_id = business_id, run_id

        def before(self, request, call_no):
            events.append(
                ("before", request.task, self.run_id, self.business_id, request.fixture_scenario)
            )
            return object()

        def after(self, *_a):
            events.append(("after",))

    monkeypatch.setattr(config, "MOCK_LLM", False)
    monkeypatch.setattr(config, "CLAUDE_MODEL_STRONG", "strong-model")
    monkeypatch.setattr(budget, "LLMGuard", FakeGuard)

    def llm(task, schema, prompt, **kw):
        # fixtures come from the mock provider; the guard path is the real one
        return run_structured(task, schema, prompt, provider=MockProvider(), **kw)

    bid = new_business(session_factory, idea("good"))
    with session_factory() as s:
        repository.claim_next(s, "w1", 60)
    from app.pipeline.runner import run_business

    run_business(session_factory, bid, worker_id="w1", stages=m3_stages(llm))
    befores = [e for e in events if e[0] == "before"]
    assert [e[1] for e in befores] == ["premortem", "assumption_extraction"]
    assert len({e[2] for e in befores}) == 1 and befores[0][2] not in (None, "-")
    assert all(e[3] == bid for e in befores)
    assert all(e[4] is None for e in befores)  # real calls ignore fixture overrides
    assert get(session_factory, bid).status == "GATED"


def test_real_mode_without_run_id_fails_closed(session_factory, monkeypatch) -> None:
    from app import config
    from app.agents.premortem import run_premortem

    monkeypatch.setattr(config, "MOCK_LLM", False)
    bid = new_business(session_factory, idea("good"))
    with session_factory() as s, pytest.raises(RunIdRequired):
        run_premortem(get_business(s, bid), run_id="-")


def get_business(s, bid):
    return s.get(__import__("app.models", fromlist=["Business"]).Business, bid)


def test_starting_budget_defaults_to_high_startup_cost_and_is_labelled(session_factory) -> None:
    bid = new_business(session_factory, idea("good"))
    run(session_factory, bid)
    d = scorecards(session_factory, bid)[0].details_json
    assert d["starting_budget_source"] == "assumed_equal_to_high_startup_cost"
    assert d["starting_budget"] == 100000  # the good fixture's startup_cost.high


def test_founder_budget_is_used_when_given(session_factory) -> None:
    bid = new_business(session_factory, idea("good"), budget=300000)
    run(session_factory, bid)
    d = scorecards(session_factory, bid)[0].details_json
    assert (d["starting_budget_source"], d["starting_budget"]) == ("founder", 300000)


# --- lease safety of mid-stage writes ------------------------------------------------------


class LeaseStealingLLM(SpyLLM):
    """During the pre-mortem call, let the lease lapse and another worker claim the business."""

    def __init__(self, sf) -> None:
        super().__init__()
        self.sf, self.stolen = sf, False

    def __call__(self, task, schema, prompt, **kw):
        if task == "premortem" and not self.stolen:
            self.stolen = True
            from sqlalchemy import func, update

            from app.models import Business

            with self.sf() as s:
                s.execute(
                    update(Business)
                    .where(Business.id == kw["business_id"])
                    .values(locked_until=func.now() - func.make_interval(0, 0, 0, 0, 0, 0, 5))
                )
                s.commit()
                assert repository.claim_next(s, "w2", 60) is not None  # w2 now holds the lease
        return super().__call__(task, schema, prompt, **kw)


def test_worker_that_lost_its_lease_writes_nothing_at_the_checkpoint(session_factory) -> None:
    llm = LeaseStealingLLM(session_factory)
    bid = new_business(session_factory, idea("good"))
    assert run(session_factory, bid, llm, worker="w1").value == "PREMORTEM"  # abandoned quietly
    b = get(session_factory, bid)
    assert b.status == "PREMORTEM" and b.locked_by == "w2"  # not FAILED, w2's lease intact
    assert b.premortem_json is None and not assumptions(session_factory, bid)
    assert b.attempts == 0 and b.last_error is None

    # the new owner completes the stage; exactly one scorecard, one set of assumptions
    from app.pipeline.runner import run_business

    run_business(session_factory, bid, worker_id="w2", stages=m3_stages(SpyLLM()))
    assert get(session_factory, bid).status == "GATED"
    assert (
        len(scorecards(session_factory, bid)) == 1 and len(assumptions(session_factory, bid)) == 6
    )


def test_checkpoint_refuses_when_the_status_changed_underneath(session_factory) -> None:
    class ChangeStatusLLM(SpyLLM):
        def __call__(self, task, schema, prompt, **kw):
            if task == "premortem":
                with session_factory() as s:
                    status.fail(s, kw["business_id"], actor=Actor.user(), error="x", worker_id="w1")
                    s.commit()
            return super().__call__(task, schema, prompt, **kw)

    bid = new_business(session_factory, idea("good"))
    run(session_factory, bid, ChangeStatusLLM())
    b = get(session_factory, bid)
    assert b.status == "FAILED" and b.premortem_json is None


def test_checkpoint_extends_the_lease(session_factory) -> None:
    from sqlalchemy import func, update

    from app.models import Business

    seen = {}

    class Probe(SpyLLM):
        def __call__(self, task, schema, prompt, **kw):
            if task == "assumption_extraction":
                with session_factory() as s:  # after the pre-mortem checkpoint
                    seen["after_checkpoint"] = s.get(Business, kw["business_id"]).locked_until
            return super().__call__(task, schema, prompt, **kw)

    bid = new_business(session_factory, idea("good"))
    with session_factory() as s:
        repository.claim_next(s, "w1", 5)  # short initial lease
        s.execute(
            update(Business)
            .where(Business.id == bid)
            .values(locked_until=func.now() + func.make_interval(0, 0, 0, 0, 0, 0, 5))
        )
        s.commit()
        soon = s.get(Business, bid).locked_until
    from app.pipeline.runner import run_business

    run_business(session_factory, bid, worker_id="w1", stages=m3_stages(Probe()))
    assert seen["after_checkpoint"] > soon  # lease pushed out to a fresh TTL
