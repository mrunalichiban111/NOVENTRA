"""Cost calculation, usage persistence and the per-run spend cap.

Sections marked [DB] need TEST_DATABASE_URL (a database whose name ends in _test) via the
`session_factory` fixture; everything else is pure. No test calls the real Anthropic API.
"""

import threading
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from app import config
from app.llm import pricing, structured
from app.llm.base import (
    BudgetExceeded,
    InputTooLarge,
    LLMRequest,
    LLMResponse,
    PricingMissing,
    RefusalError,
    RunIdRequired,
    TransportError,
)
from app.llm.budget import LLMGuard, estimate_input_tokens, worst_case_usd
from app.llm.schemas import PreMortem
from app.llm.usage import spend_for_run, spend_last_24h_usd
from app.models import Business, LlmBudgetReservation, LlmUsage
from tests.llm_helpers import ScriptedProvider, tool_response, valid_for

HAIKU = "claude-haiku-5-5"


def req(model: str = HAIKU, user: str = "hello", max_tokens: int = 1000, **kw) -> LLMRequest:
    return LLMRequest("premortem", model, "sys", user, max_tokens, 30.0, **kw)


# --- pure: pricing and estimates -----------------------------------------------------


def test_cost_calculation_all_four_components():
    # Sonnet: 1M in * $2 + 1M out * $10 + 1M read * $0.10 + 1M write * $2.50
    cost = pricing.estimate_cost_usd(
        "claude-sonnet-5-5",
        input_tokens=1_000_000,
        output_tokens=1_000_000,
        cache_read_tokens=1_000_000,
        cache_write_tokens=1_000_000,
    )
    assert cost == pytest.approx(14.60)
    h = pricing.estimate_cost_usd(HAIKU, input_tokens=2_000_000, output_tokens=100_000)
    assert h == pytest.approx(0.20 + 0.05)


def test_missing_pricing_fails_closed():
    with pytest.raises(PricingMissing):
        pricing.estimate_cost_usd("some-unpriced-model", input_tokens=1, output_tokens=1)
    with pytest.raises(PricingMissing):
        worst_case_usd(req(model="some-unpriced-model"))


def test_worst_case_is_input_plus_full_max_tokens():
    r = req(max_tokens=2000)
    expected = estimate_input_tokens(r) * 0.10 / 1e6 + 2000 * 0.50 / 1e6
    assert float(worst_case_usd(r)) == pytest.approx(expected, abs=2e-6)


def test_oversized_input_is_refused():
    with pytest.raises(InputTooLarge):
        worst_case_usd(req(user="x" * 400_000))


# --- [DB] usage persistence ----------------------------------------------------------


def guard(factory: sessionmaker[Session], run_id: str | None = "run-1", cap: float = 2.0, **kw):
    return LLMGuard(
        business_id=kw.pop("business_id", None),
        run_id=run_id,
        session_factory=factory,
        cap_usd=cap,
        **kw,
    )


def usage_rows(factory: sessionmaker[Session]) -> list[LlmUsage]:
    with factory() as s:
        return list(s.scalars(select(LlmUsage).order_by(LlmUsage.attempt)))


def test_usage_rows_for_failed_validation_then_success(session_factory):
    bad = valid_for("premortem")
    del bad["target_customer"]
    p = ScriptedProvider(
        tool_response(bad, input_tokens=1000, output_tokens=200, latency_ms=5),
        tool_response(
            valid_for("premortem"),
            input_tokens=1000,
            output_tokens=300,
            cache_read_tokens=50,
            cache_write_tokens=10,
            request_id="req_x",
        ),
    )
    p.name = "claude"
    config_patch = pytest.MonkeyPatch()
    config_patch.setattr(config, "MOCK_LLM", False)
    config_patch.setattr(config, "CLAUDE_MODEL_STRONG", HAIKU)
    try:
        structured.run_structured(
            "premortem", PreMortem, "idea", provider=p, sleep=lambda s: None,
            guard=guard(session_factory, "run-a"), run_id="run-a",
        )  # fmt: skip
    finally:
        config_patch.undo()
    rows = usage_rows(session_factory)
    assert [(r.attempt, r.status, r.error_category) for r in rows] == [
        (1, "failure", "validation"),
        (2, "success", None),
    ]
    assert rows[0].input_tokens == 1000 and rows[0].output_tokens == 200
    assert (rows[1].cache_read_tokens, rows[1].cache_write_tokens) == (50, 10)
    assert rows[0].estimated_cost_usd == Decimal("0.000200")  # 1000*0.10/1e6 + 200*0.5/1e6
    assert rows[1].estimated_cost_usd == Decimal("0.000252")
    assert {r.run_id for r in rows} == {"run-a"} and rows[0].task == "premortem"
    with session_factory() as s:
        assert spend_for_run(s, "run-a") == Decimal("0.000452")
        assert {r.status for r in s.scalars(select(LlmBudgetReservation))} == {"settled"}


def test_usage_survives_outer_transaction_rollback(session_factory):
    outer = session_factory()
    try:
        outer.add(Business(id=uuid.uuid4(), idea_text="x"))
        outer.flush()
        g = guard(session_factory, "run-r")
        t = g.before(req(), 1)
        resp = tool_response({}, input_tokens=10, output_tokens=10)
        g.after(t, req(), 1, resp, "failure", "validation")
        outer.rollback()  # simulates a later pipeline stage failing
    finally:
        outer.close()
    assert len(usage_rows(session_factory)) == 1


def test_business_fk_set_null_on_delete(session_factory):
    with session_factory() as s:
        b = Business(id=uuid.uuid4(), idea_text="x")
        s.add(b)
        s.commit()
        bid = b.id
    g = guard(session_factory, "run-b", business_id=bid)
    g.after(g.before(req(), 1), req(), 1, tool_response({}, input_tokens=1), "success", None)
    with session_factory() as s:
        s.delete(s.get(Business, bid))
        s.commit()
    assert usage_rows(session_factory)[0].business_id is None


def test_transport_failure_releases_reservation_and_writes_no_usage(session_factory):
    g = guard(session_factory, "run-t")
    p = ScriptedProvider(
        TransportError("503", retryable=True),
        tool_response(valid_for("premortem"), input_tokens=10, output_tokens=10),
    )
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "MOCK_LLM", False)
    mp.setattr(config, "CLAUDE_MODEL_STRONG", HAIKU)
    try:
        structured.run_structured(
            "premortem", PreMortem, "idea", provider=p, sleep=lambda s: None, guard=g
        )
    finally:
        mp.undo()
    assert len(p.requests) == 2 and len(usage_rows(session_factory)) == 1
    with session_factory() as s:  # every attempt (incl. transport retry) had its own reservation
        statuses = sorted(r.status for r in s.scalars(select(LlmBudgetReservation)))
    assert statuses == ["released", "settled"]


def test_spend_last_24h(session_factory):
    g = guard(session_factory, "run-24")
    g.after(
        g.before(req(), 1), req(), 1, tool_response({}, input_tokens=1_000_000), "success", None
    )
    with session_factory() as s:  # an old row must be excluded
        s.add(
            LlmUsage(
                id=uuid.uuid4(), task="t", provider="claude", model=HAIKU, attempt=1,
                estimated_cost_usd=Decimal("5"), status="success",
                created_at=datetime.now(UTC) - timedelta(hours=25),
            )
        )  # fmt: skip
        s.commit()
    assert spend_last_24h_usd(session_factory) == Decimal("0.100000")


# --- [DB] budget cap -----------------------------------------------------------------


def test_cap_blocks_without_calling_provider(session_factory):
    r = req()
    one = worst_case_usd(r)
    g = guard(session_factory, "run-c", cap=float(one) * 1.5)
    t1 = g.before(r, 1)  # fits
    with pytest.raises(BudgetExceeded):
        g.before(r, 2)  # active reservation counts: 2x worst-case > cap
    g.after(t1, r, 1, None, "failure", "transport_rejected")  # release frees the headroom
    assert g.before(r, 3)


def test_settled_spend_counts_toward_cap(session_factory):
    r = req(max_tokens=1000)
    g = guard(session_factory, "run-s", cap=0.0007)  # worst-case ~0.0005
    t = g.before(r, 1)
    g.after(
        t, r, 1, tool_response({}, input_tokens=0, output_tokens=1000), "success", None
    )  # $0.0005
    with pytest.raises(BudgetExceeded):
        g.before(r, 2)  # 0.0005 settled + ~0.0005 worst-case > 0.0007


def test_blocked_run_makes_no_provider_call_via_run_structured(session_factory):
    mp = pytest.MonkeyPatch()
    mp.setattr(config, "MOCK_LLM", False)
    mp.setattr(config, "CLAUDE_MODEL_STRONG", HAIKU)
    p = ScriptedProvider(tool_response(valid_for("premortem")))
    try:
        with pytest.raises(BudgetExceeded):
            structured.run_structured(
                "premortem",
                PreMortem,
                "idea",
                provider=p,
                guard=guard(session_factory, "r", cap=0.0),
            )
    finally:
        mp.undo()
    assert p.requests == []


def test_expired_reservations_stop_counting(session_factory):
    r = req()
    one = worst_case_usd(r)
    g = guard(session_factory, "run-e", cap=float(one) * 1.5)
    g.before(r, 1)
    with pytest.raises(BudgetExceeded):
        g.before(r, 2)
    with session_factory() as s:  # simulate a crashed worker: reservation is now past expiry
        s.execute(
            update(LlmBudgetReservation).values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        s.commit()
    assert g.before(r, 3)


def test_caps_are_per_run(session_factory):
    r = req()
    cap = float(worst_case_usd(r)) * 1.5
    guard(session_factory, "run-x", cap=cap).before(r, 1)
    assert guard(session_factory, "run-y", cap=cap).before(r, 1)


def test_concurrent_calls_in_one_run_cannot_both_pass(session_factory):
    r = req()
    cap = float(worst_case_usd(r)) * 1.5  # room for exactly one worst-case attempt
    n = 8
    barrier = threading.Barrier(n)
    results: list[str] = []
    lock = threading.Lock()

    def worker() -> None:
        g = guard(session_factory, "run-race", cap=cap)
        barrier.wait()
        try:
            g.before(r, 1)
            outcome = "ok"
        except BudgetExceeded:
            outcome = "blocked"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker) for _ in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["blocked"] * (n - 1) + ["ok"]


# --- [DB] corrections: run_id, ambiguous failures, settlement ------------------------


@pytest.fixture
def real_mode(monkeypatch):
    monkeypatch.setattr(config, "MOCK_LLM", False)
    monkeypatch.setattr(config, "CLAUDE_MODEL_STRONG", HAIKU)


def reservations(factory) -> list[LlmBudgetReservation]:
    with factory() as s:
        return list(
            s.scalars(select(LlmBudgetReservation).order_by(LlmBudgetReservation.created_at))
        )


def run(factory, provider, run_id="run-q", cap=2.0, **kw):
    return structured.run_structured(
        "premortem", PreMortem, "idea", provider=provider, sleep=lambda s: None,
        guard=guard(factory, run_id, cap=cap), **kw,
    )  # fmt: skip


def test_guard_cannot_be_built_without_run_id(session_factory):
    for bad in (None, "", "-"):
        with pytest.raises(RunIdRequired):
            LLMGuard(business_id=None, run_id=bad, session_factory=session_factory)


def test_timeout_after_send_retains_reservation_and_is_not_retried(session_factory, real_mode):
    p = ScriptedProvider(
        TransportError("timed out", retryable=False, outcome="ambiguous"),
        tool_response(valid_for("premortem")),
    )
    with pytest.raises(TransportError):
        run(session_factory, p)
    assert len(p.requests) == 1  # ambiguous outcomes are never auto-retried
    (res,) = reservations(session_factory)
    assert res.status == "settled"  # kept, counted at the worst-case estimate; never released
    (row,) = usage_rows(session_factory)
    assert (row.status, row.error_category) == ("failure", "transport_ambiguous")
    assert row.estimated_cost_usd == res.amount_usd and row.input_tokens == 0
    # a later attempt in the same run still goes through the budget check, with that spend counted
    r = req()
    with pytest.raises(BudgetExceeded):
        guard(session_factory, "run-q", cap=float(res.amount_usd) * 1.2).before(r, 9)
    assert guard(session_factory, "run-q", cap=float(res.amount_usd) * 3).before(r, 9)


def test_known_pre_send_failure_releases_reservation(session_factory, real_mode):
    p = ScriptedProvider(
        TransportError("no route", retryable=True, outcome="not_sent"),
        tool_response(valid_for("premortem"), input_tokens=10, output_tokens=10),
    )
    run(session_factory, p)
    assert [r.status for r in reservations(session_factory)] == ["released", "settled"]
    assert len(usage_rows(session_factory)) == 1  # nothing recorded/charged for the unsent try
    with session_factory() as s:
        assert spend_for_run(s, "run-q") < Decimal("0.0001")


def test_success_settles_at_actual_not_worst_case(session_factory, real_mode):
    p = ScriptedProvider(tool_response(valid_for("premortem"), input_tokens=100, output_tokens=100))
    run(session_factory, p)
    (res,) = reservations(session_factory)
    (row,) = usage_rows(session_factory)
    assert res.status == "settled" and row.status == "success"
    assert row.estimated_cost_usd == Decimal("0.000060") < res.amount_usd  # 100*.10/1e6+100*.5/1e6


def test_known_provider_error_with_usage_is_settled_with_that_usage(session_factory, real_mode):
    refusal = RefusalError(
        "declined", response=tool_response({}, input_tokens=500, output_tokens=7)
    )
    with pytest.raises(RefusalError):
        run(session_factory, ScriptedProvider(refusal))
    (res,) = reservations(session_factory)
    (row,) = usage_rows(session_factory)
    assert res.status == "settled" and (row.status, row.error_category) == ("failure", "refusal")
    assert row.input_tokens == 500 and row.estimated_cost_usd == Decimal("0.000054")


def test_provider_rejection_releases(session_factory, real_mode):
    p = ScriptedProvider(
        TransportError("401", retryable=False, status_code=401, outcome="rejected")
    )
    with pytest.raises(TransportError):
        run(session_factory, p)
    assert [r.status for r in reservations(session_factory)] == ["released"]
    assert usage_rows(session_factory) == []


def test_after_cannot_double_settle_or_release(session_factory):
    g = guard(session_factory, "run-d")
    r = req()
    t = g.before(r, 1)
    resp = tool_response({}, input_tokens=100, output_tokens=100)
    g.after(t, r, 1, resp, "success", None)
    g.after(t, r, 1, resp, "success", None)  # duplicate settle
    g.after(t, r, 1, None, "failure", "transport_rejected")  # release after settle
    g2 = g.before(r, 2)
    g.after(g2, r, 2, None, "failure", "transport_rejected")
    g.after(g2, r, 2, resp, "success", None)  # settle after release
    assert len(usage_rows(session_factory)) == 1
    assert sorted(x.status for x in reservations(session_factory)) == ["released", "settled"]


def test_validation_retry_uses_same_run_id_and_a_fresh_budget_check(session_factory, real_mode):
    bad = valid_for("premortem")
    del bad["target_customer"]
    first = tool_response(bad, input_tokens=0, output_tokens=1000)  # settles at $0.0005
    # (a) cap has room for both attempts: both reservations/usage rows carry the same run_id
    p = ScriptedProvider(
        first, tool_response(valid_for("premortem"), input_tokens=1, output_tokens=1)
    )
    run(session_factory, p, run_id="run-v", cap=2.0)
    assert len(p.requests) == 2
    assert {x.run_id for x in reservations(session_factory)} == {"run-v"}
    assert {u.run_id for u in usage_rows(session_factory)} == {"run-v"}
    assert len(reservations(session_factory)) == 2  # a fresh reservation per attempt
    # (b) cap fits only the first attempt: the repair is blocked by a fresh check, no provider call
    p2 = ScriptedProvider(first, tool_response(valid_for("premortem")))
    with pytest.raises(BudgetExceeded):
        run(session_factory, p2, run_id="run-w", cap=0.0024)
    assert len(p2.requests) == 1


# --- [DB] daily cap (M5): rolling 24h across runs, concurrency-safe, no double counting -----


def _resp(model: str = HAIKU, out: int = 100) -> LLMResponse:
    return LLMResponse(model=model, input_tokens=100, output_tokens=out)


def test_daily_cap_race_across_runs_admits_only_what_fits(session_factory):
    r = req()
    amount = float(worst_case_usd(r))
    daily = amount * 2.5  # room for exactly two attempts across all runs
    n = 8
    barrier = threading.Barrier(n)
    results: list[str] = []
    lock = threading.Lock()

    def worker(i: int) -> None:
        g = guard(session_factory, f"run-daily-{i}", cap=2.0, daily_cap_usd=daily)
        barrier.wait()
        try:
            g.before(r, 1)
            outcome = "ok"
        except BudgetExceeded as exc:
            assert "daily spend cap" in str(exc)
            outcome = "blocked"
        with lock:
            results.append(outcome)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["blocked"] * (n - 2) + ["ok"] * 2


def test_daily_cap_counts_settled_usage_once_and_released_not_at_all(session_factory):
    r = req()
    amount = worst_case_usd(r)
    g = guard(session_factory, "run-d1", cap=2.0, daily_cap_usd=float(amount) * 10)
    t1 = g.before(r, 1)
    g.after(t1, r, 1, _resp(), "success", None)  # settled: usage row only
    t2 = g.before(r, 2)
    g.after(t2, r, 2, None, "failure", "transport_not_sent")  # released: counts nothing
    with session_factory() as s:
        from app.llm.budget import _DAILY_SPEND_SQL

        counted = Decimal(s.execute(_DAILY_SPEND_SQL).scalar_one())
        active = s.scalars(
            select(LlmBudgetReservation).where(LlmBudgetReservation.status == "active")
        ).all()
    usage_total = sum(u.estimated_cost_usd for u in usage_rows(session_factory))
    assert active == [] and counted == usage_total  # no reservation counted twice
    assert len(usage_rows(session_factory)) == 1


def test_daily_cap_default_and_override(monkeypatch, session_factory):
    assert config.DEFAULT_DAILY_CAP_USD == "5.00"
    r = req()
    monkeypatch.setattr(config, "DAILY_SPEND_CAP_USD", float(worst_case_usd(r)) / 2)
    with pytest.raises(BudgetExceeded, match="daily spend cap"):
        guard(session_factory, "run-d2", cap=2.0).before(r, 1)


def test_five_concurrent_calls_one_run_reserve_and_settle_once(session_factory):
    r = req()
    n = 5
    barrier = threading.Barrier(n)

    def worker(i: int) -> None:
        g = guard(session_factory, "run-five", cap=2.0)
        barrier.wait()
        tok = g.before(r, i)
        g.after(tok, r, i, _resp(), "success", None)
        g.after(tok, r, i, _resp(), "success", None)  # duplicate settle is a no-op

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(1, n + 1)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    with session_factory() as s:
        res = s.scalars(select(LlmBudgetReservation)).all()
    assert len(res) == n and all(x.status == "settled" for x in res)
    assert len(usage_rows(session_factory)) == n
