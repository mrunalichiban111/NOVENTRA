import json
import threading
import uuid
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session, sessionmaker

from app import worker
from app.models import AuditLog, Business, BusinessStatus
from app.pipeline import repository, status
from app.pipeline.runner import StageResult, run_business
from app.pipeline.status import PIPELINE_ORDER, Actor
from app.pipeline.stubs import default_stages

S = BusinessStatus
FIXTURES = Path(__file__).resolve().parent.parent.parent / "fixtures" / "ideas"
HAPPY = [s.value for s in PIPELINE_ORDER]
TTL = 60


def idea(name: str) -> str:
    return json.loads((FIXTURES / f"{name}.json").read_text())["idea"]


def new_business(sf: sessionmaker[Session], text: str | None = None) -> uuid.UUID:
    with sf() as s:
        b = repository.create_business(
            s,
            idea_text=text or idea("viable"),
            market="India",
            price_guess=None,
            starting_budget=None,
            actor=Actor.user(),
        )
        s.commit()
        return b.id


def claim(sf: sessionmaker[Session], worker_id: str = "w1") -> uuid.UUID | None:
    with sf() as s:
        b = repository.claim_next(s, worker_id, TTL)
        return b.id if b else None


def expire_lease(sf: sessionmaker[Session], bid: uuid.UUID) -> None:
    with sf() as s:
        s.execute(
            update(Business)
            .where(Business.id == bid)
            .values(locked_until=func.now() - func.make_interval(0, 0, 0, 0, 0, 0, 5))
        )
        s.commit()


def snapshot(sf: sessionmaker[Session], bid: uuid.UUID) -> Business:
    with sf() as s:
        return s.get(Business, bid)


def transitions(sf: sessionmaker[Session], bid: uuid.UUID) -> list[tuple[str | None, str | None]]:
    with sf() as s:
        rows = s.scalars(select(AuditLog).where(AuditLog.business_id == bid).order_by(AuditLog.id))
        return [(r.from_status, r.to_status) for r in rows]


def counting(stages: dict, counter: Counter) -> dict:
    def wrap(stage: BusinessStatus, fn):
        def handler(session, business):
            counter[stage] += 1
            return fn(session, business)

        return handler

    return {k: wrap(k, v) for k, v in stages.items()}


def test_happy_path_runs_every_stage_once(session_factory) -> None:
    bid = new_business(session_factory)
    assert claim(session_factory) == bid
    final = run_business(session_factory, bid, worker_id="w1", stages=default_stages())
    assert final == S.DONE
    b = snapshot(session_factory, bid)
    assert (b.status, b.locked_by, b.locked_until) == ("DONE", None, None)
    pairs = transitions(session_factory, bid)
    assert [t for _, t in pairs] == HAPPY
    assert pairs[0] == (None, "INTAKE")
    with session_factory() as s:
        live = s.scalars(select(AuditLog).where(AuditLog.to_status == "LIVE")).one()
        assert live.version_ref == "v1" and live.actor == "worker:w1"
        assert live.action == "stage.deploying" and live.reason


def test_rejected_verdict_is_terminal(session_factory) -> None:
    bid = new_business(session_factory, idea("weak"))
    claim(session_factory)
    assert run_business(session_factory, bid, worker_id="w1", stages=default_stages()) == S.REJECTED
    assert [t for _, t in transitions(session_factory, bid)] == ["INTAKE", "PREMORTEM", "REJECTED"]
    assert claim(session_factory) is None  # terminal: never claimed again


def test_claim_is_exclusive_under_concurrency(session_factory) -> None:
    ids = {new_business(session_factory) for _ in range(3)}
    results: list[uuid.UUID | None] = []
    lock = threading.Lock()
    barrier = threading.Barrier(10)

    def go(n: int) -> None:
        barrier.wait()
        got = claim(session_factory, f"w{n}")
        with lock:
            results.append(got)

    threads = [threading.Thread(target=go, args=(n,)) for n in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    won = [r for r in results if r is not None]
    assert len(won) == 3 and set(won) == ids  # each business claimed exactly once


def test_claim_skips_leased_failed_and_terminal_but_reclaims_expired(session_factory) -> None:
    leased = new_business(session_factory)
    assert claim(session_factory, "a") == leased
    assert claim(session_factory, "b") is None  # live lease
    expire_lease(session_factory, leased)
    assert claim(session_factory, "b") == leased  # crashed worker's lease lapsed

    with session_factory() as s:
        status.fail(s, leased, actor=Actor.user(), error="x")
        s.commit()
    assert claim(session_factory, "c") is None  # FAILED is not auto-retried


def test_graceful_stop_then_resume_runs_each_stage_once(session_factory) -> None:
    bid = new_business(session_factory)
    calls: Counter = Counter()
    stages = counting(default_stages(), calls)

    claim(session_factory, "w1")
    done = 0

    def stop_after_three() -> bool:
        nonlocal done
        done += 1
        return done > 3

    assert (
        run_business(
            session_factory, bid, worker_id="w1", stages=stages, should_stop=stop_after_three
        )
        == S.DOMAIN_PENDING
    )
    assert snapshot(session_factory, bid).locked_by is None  # lease released on stop

    assert claim(session_factory, "w2") == bid
    assert run_business(session_factory, bid, worker_id="w2", stages=stages) == S.DONE
    assert all(calls[s] == 1 for s in PIPELINE_ORDER[:-1])  # nothing repeated
    assert [t for _, t in transitions(session_factory, bid)] == HAPPY  # no duplicate audit rows


class Crash(BaseException):
    """Simulates the process dying mid-stage (not caught by `except Exception`)."""


def test_hard_crash_mid_stage_resumes_from_last_committed_stage(session_factory) -> None:
    bid = new_business(session_factory)
    stages = default_stages()
    calls: Counter = Counter()
    crashing = counting(stages, calls)
    crashing[S.DEPLOYING] = lambda *_: (_ for _ in ()).throw(Crash())

    claim(session_factory, "w1")
    with pytest.raises(Crash):
        run_business(session_factory, bid, worker_id="w1", stages=crashing)
    assert snapshot(session_factory, bid).status == "DEPLOYING"  # earlier stages were committed
    assert [t for _, t in transitions(session_factory, bid)] == HAPPY[:5]

    expire_lease(session_factory, bid)
    assert claim(session_factory, "w2") == bid
    assert (
        run_business(session_factory, bid, worker_id="w2", stages=counting(stages, calls)) == S.DONE
    )
    assert [t for _, t in transitions(session_factory, bid)] == HAPPY
    assert calls[S.INTAKE] == calls[S.PREMORTEM] == calls[S.GATED] == calls[S.DOMAIN_PENDING] == 1


def test_worker_that_lost_its_lease_cannot_commit(session_factory) -> None:
    bid = new_business(session_factory)
    claim(session_factory, "slow")
    stages = default_stages()
    inner = stages[S.INTAKE]

    def slow_intake(session, business):
        result = inner(session, business)
        expire_lease(session_factory, bid)  # lease lapses while "slow" works...
        assert claim(session_factory, "fast") == bid  # ...and another worker takes over
        return result

    stages[S.INTAKE] = slow_intake
    run_business(session_factory, bid, worker_id="slow", stages=stages)
    assert snapshot(session_factory, bid).status == "INTAKE"  # slow's transition was refused
    assert transitions(session_factory, bid) == [(None, "INTAKE")]

    assert run_business(session_factory, bid, worker_id="fast", stages=default_stages()) == S.DONE
    assert [t for _, t in transitions(session_factory, bid)] == HAPPY


def failing(stage: BusinessStatus, times: int, calls: Counter):
    stages = counting(default_stages(), calls)
    inner = stages[stage]

    def handler(session, business):
        if calls[("fail", stage)] < times:
            calls[("fail", stage)] += 1
            raise RuntimeError("stub stage exploded")
        return inner(session, business)

    stages[stage] = handler
    return stages


def test_failure_is_recorded_and_retry_resumes_failed_stage_only(session_factory) -> None:
    bid = new_business(session_factory)
    calls: Counter = Counter()
    stages = failing(S.DEPLOYING, 1, calls)

    claim(session_factory, "w1")
    assert run_business(session_factory, bid, worker_id="w1", stages=stages) == S.FAILED
    b = snapshot(session_factory, bid)
    assert (b.status, b.failed_stage, b.attempts, b.locked_by) == ("FAILED", "DEPLOYING", 1, None)
    assert "RuntimeError" in b.last_error and "exploded" in b.last_error
    with session_factory() as s:
        entry = s.scalars(select(AuditLog).where(AuditLog.action == "pipeline.failed")).one()
        assert (entry.from_status, entry.to_status, entry.actor) == (
            "DEPLOYING",
            "FAILED",
            "worker:w1",
        )

    assert claim(session_factory, "w1") is None  # no automatic retry

    with session_factory() as s:
        status.retry(s, bid, actor=Actor.user())
        s.commit()
    assert claim(session_factory, "w2") == bid
    assert run_business(session_factory, bid, worker_id="w2", stages=stages) == S.DONE
    assert calls[S.INTAKE] == calls[S.PREMORTEM] == calls[S.GATED] == 1  # not repeated
    assert [t for _, t in transitions(session_factory, bid)] == [
        *HAPPY[:5], "FAILED", "DEPLOYING", *HAPPY[5:],
    ]  # fmt: skip


def test_retries_are_bounded(session_factory) -> None:
    bid = new_business(session_factory)
    stages = failing(S.INTAKE, 99, Counter())
    for n in range(3):
        assert claim(session_factory, "w") == bid
        assert run_business(session_factory, bid, worker_id="w", stages=stages) == S.FAILED
        with session_factory() as s:
            if n < 2:
                status.retry(s, bid, actor=Actor.user(), max_attempts=3)
                s.commit()
    with session_factory() as s, pytest.raises(status.RetryLimitExceeded):
        status.retry(s, bid, actor=Actor.user(), max_attempts=3)
    assert claim(session_factory) is None
    assert snapshot(session_factory, bid).attempts == 3


def test_failing_handler_writes_are_rolled_back(session_factory) -> None:
    bid = new_business(session_factory)
    stages = default_stages()

    def dirty_then_fail(session, business):
        business.target_customer = "should not persist"
        session.flush()
        raise ValueError("nope")

    stages[S.INTAKE] = dirty_then_fail
    claim(session_factory)
    assert run_business(session_factory, bid, worker_id="w1", stages=stages) == S.FAILED
    assert snapshot(session_factory, bid).target_customer is None


def test_handler_result_carries_reason_and_version(session_factory) -> None:
    bid = new_business(session_factory)
    stages = default_stages()
    stages[S.INTAKE] = lambda *_: StageResult(S.PREMORTEM, "custom reason", "v9")
    claim(session_factory)
    run_business(
        session_factory,
        bid,
        worker_id="w1",
        stages=stages,
        should_stop=iter([False, True]).__next__,
    )
    with session_factory() as s:
        e = s.scalars(select(AuditLog).where(AuditLog.to_status == "PREMORTEM")).one()
        assert (e.reason, e.version_ref) == ("custom reason", "v9")


def test_work_once_uses_claim_and_runs_to_completion(session_factory, monkeypatch) -> None:
    monkeypatch.setattr(worker, "SessionLocal", session_factory)
    worker.stop_event.clear()
    bid = new_business(session_factory)
    assert worker.work_once("w1", stages=default_stages()) is True  # M1 stub pipeline
    assert snapshot(session_factory, bid).status == "DONE"
    assert worker.work_once("w1", stages=default_stages()) is False  # nothing left to do
