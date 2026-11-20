"""M3 pre-mortem lease duration (M4B fix): the lease must cover each model call, and losing it
during a call must still write nothing. *_test databases only; mock LLM; no network."""

from sqlalchemy import select, text, update

from app.agents import moderation
from app.models import Assumption, Business
from app.pipeline import repository
from app.pipeline.checkpoint import model_call_lease_s
from app.pipeline.handlers import m3_stages
from app.pipeline.runner import run_business
from tests.m3_helpers import SpyLLM, get, idea, new_business, run

OLD_LEASE_S = 60  # WORKER_CLAIM_TTL_SECONDS: all the old code held during a model call


def lease_left_s(sf, bid) -> float:  # noqa: ANN001
    with sf() as s:
        return float(
            s.scalar(
                text(
                    "SELECT extract(epoch FROM locked_until - now()) FROM businesses WHERE id = :b"
                ),
                {"b": bid},
            )
        )


def steal(sf, bid, to: str = "w2") -> None:  # noqa: ANN001
    with sf() as s:
        s.execute(
            update(Business)
            .where(Business.id == bid)
            .values(locked_by=None, locked_until=text("now() - interval '1 second'"))
        )
        s.commit()
        assert repository.claim_next(s, to, 60) is not None


def test_lease_covers_each_model_call_beyond_the_old_60s(session_factory) -> None:
    seen: dict[str, float] = {}
    blocked: dict[str, bool] = {}

    class Probe(SpyLLM):
        def __call__(self, task, schema, prompt, **kw):  # noqa: ANN001, ANN003, ANN204
            bid = kw["business_id"]
            seen[task] = lease_left_s(session_factory, bid)
            with session_factory() as s:
                # Would the lease still be ours if this call ran longer than the old 60s?
                still_valid = s.scalar(
                    select(Business.locked_until > text("now() + interval '61 seconds'")).where(
                        Business.id == bid
                    )
                )
                # A competing worker cannot take the business mid-call.
                blocked[task] = still_valid and repository.claim_next(s, "w2", 60) is None
            return super().__call__(task, schema, prompt, **kw)

    bid = new_business(session_factory, idea("good"))
    assert run(session_factory, bid, Probe()).value == "GATED"
    for task in ("premortem", "assumption_extraction"):
        assert seen[task] > OLD_LEASE_S
        assert seen[task] <= model_call_lease_s(task) + 1
        assert blocked[task]
    b = get(session_factory, bid)
    assert b.locked_by is None and b.premortem_json is not None  # outputs unchanged


def test_lease_lost_during_assumption_call_writes_nothing_and_keeps_premortem(
    session_factory,
) -> None:
    class StealDuringExtraction(SpyLLM):
        def __call__(self, task, schema, prompt, **kw):  # noqa: ANN001, ANN003, ANN204
            if task == "assumption_extraction":
                steal(session_factory, kw["business_id"])
            return super().__call__(task, schema, prompt, **kw)

    bid = new_business(session_factory, idea("good"))
    first = StealDuringExtraction()
    assert run(session_factory, bid, first).value == "PREMORTEM"  # ended quietly
    b = get(session_factory, bid)
    assert (b.status, b.locked_by, b.attempts, b.last_error) == ("PREMORTEM", "w2", 0, None)
    assert b.premortem_json is not None  # the earlier paid result was kept
    with session_factory() as s:
        assert s.scalars(select(Assumption).where(Assumption.business_id == bid)).all() == []

    second = SpyLLM()
    final = run_business(session_factory, bid, worker_id="w2", stages=m3_stages(second))
    assert final.value == "GATED"
    assert second.tasks() == ["assumption_extraction"]  # pre-mortem not paid for again


def test_lease_already_lost_before_the_call_means_no_paid_call(
    session_factory, monkeypatch
) -> None:
    bid = new_business(session_factory, idea("good"))
    real_check = moderation.check_idea

    def steal_then_check(text_: str):  # noqa: ANN202
        steal(session_factory, bid)  # ownership is gone after the runner's renewal
        return real_check(text_)

    monkeypatch.setattr(moderation, "check_idea", steal_then_check)
    spy = SpyLLM()
    assert run(session_factory, bid, spy).value == "PREMORTEM"
    assert spy.calls == []  # hold_lease refused before any model call
    assert get(session_factory, bid).locked_by == "w2"
