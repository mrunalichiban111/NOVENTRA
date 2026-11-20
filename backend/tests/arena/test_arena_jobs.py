"""Persona arena jobs end to end on a *_test DB (FR-AR-1..4, FR-AR-6). No network, no paid calls."""

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app.llm.schemas import PersonaEvaluation as EvalOut
from app.models import Business, PersonaEvaluation, PersonaPanel, PersonaRun, SiteVersion
from app.pipeline import arena
from tests.arena.helpers import FakeArenaLLM, configure, live_business

W = "w-arena"


@pytest.fixture(autouse=True)
def _cfg(monkeypatch: pytest.MonkeyPatch) -> None:
    configure(monkeypatch)


def _panel(sf, bid):  # noqa: ANN001, ANN202
    with sf() as s:
        return s.scalar(select(PersonaPanel).where(PersonaPanel.business_id == bid))


def _ready_panel(sf, bid, llm):  # noqa: ANN001, ANN202
    with sf() as s:
        arena.request_panel(s, bid)
    assert arena.work_once(sf, W, llm)
    return _panel(sf, bid)


def _evaluation(sf, eid) -> PersonaEvaluation:  # noqa: ANN001
    with sf() as s:
        return s.get(PersonaEvaluation, eid)


def _runs(sf, eid) -> int:  # noqa: ANN001
    with sf() as s:
        return s.scalar(select(func.count()).where(PersonaRun.evaluation_id == eid))


def test_panel_generated_once_and_idempotent(session_factory) -> None:
    sf = session_factory
    bid = live_business(sf)
    llm = FakeArenaLLM()
    panel = _ready_panel(sf, bid, llm)
    assert panel.status == "ready" and len(panel.personas_json) == 30
    assert [p["id"] for p in panel.personas_json] == [f"p{i:02d}" for i in range(1, 31)]
    with sf() as s:
        again = arena.request_panel(s, bid)
    assert again.changed is False
    assert not arena.work_once(sf, W, llm)
    assert llm.tasks() == ["persona_panel"]
    with sf() as s:
        assert s.scalar(select(func.count()).where(PersonaPanel.business_id == bid)) == 1


def test_panel_needs_live_site(session_factory) -> None:
    from tests.test_deploy_pipeline import pending_business

    bid = pending_business(session_factory)
    with session_factory() as s, pytest.raises(arena.NoLiveVersion):
        arena.request_panel(s, bid)


def test_interrupted_panel_generation_is_never_paid_again(session_factory) -> None:
    sf = session_factory
    bid = live_business(sf)
    with sf() as s:
        arena.request_panel(s, bid)
        s.execute(
            update(PersonaPanel)
            .where(PersonaPanel.business_id == bid)
            .values(status="generating", locked_until=datetime.now(UTC) - timedelta(seconds=5))
        )
        s.commit()
    llm = FakeArenaLLM()
    assert arena.work_once(sf, W, llm)
    panel = _panel(sf, bid)
    assert panel.status == "failed" and "interrupted" in panel.error
    assert llm.calls == []


def test_full_evaluation_nine_calls_concurrency_and_d(session_factory) -> None:
    sf = session_factory
    bid = live_business(sf)
    llm = FakeArenaLLM(delay=0.05)
    _ready_panel(sf, bid, llm)
    with sf() as s:
        req = arena.request_evaluation(s, bid)
        dup = arena.request_evaluation(s, bid)
    assert req.changed and not dup.changed and dup.row.id == req.row.id
    assert arena.work_once(sf, W, llm)

    assert llm.tasks().count("persona_evaluation") == 9
    assert llm.max_active <= 5
    ev = _evaluation(sf, req.row.id)
    assert ev.status == "complete" and _runs(sf, ev.id) == 90
    assert float(ev.d_score) == pytest.approx(33.33, abs=0.01)  # 10 of 30 always convert
    assert float(ev.d_se) == pytest.approx(8.75, abs=0.01)
    assert ev.evaluator_model and ev.prompt_version and ev.rubric_version == "d-v1"
    agg = ev.aggregate_json
    assert agg["simulated"] is True and agg["total_runs"] == 90 and agg["converted"] == 30
    assert agg["objections"][0]["count"] >= agg["objections"][-1]["count"]
    # Complete evaluation: another request is a no-op.
    with sf() as s:
        assert arena.request_evaluation(s, bid).changed is False


def test_failed_call_keeps_successes_and_retry_runs_only_missing(session_factory) -> None:
    sf = session_factory
    bid = live_business(sf)
    batch2 = tuple(f"p{i:02d}" for i in range(11, 21))
    llm = FakeArenaLLM(fail={batch2})
    _ready_panel(sf, bid, llm)
    with sf() as s:
        eid = arena.request_evaluation(s, bid).row.id
    arena.work_once(sf, W, llm)
    ev = _evaluation(sf, eid)
    assert ev.status == "incomplete" and ev.d_score is None
    assert ev.error_code == "evaluator_unavailable"
    assert _runs(sf, eid) == 60  # batch 2 failed for all 3 repeats (and its 2 extra rounds)

    llm.fail.clear()
    before = len(llm.calls)
    with sf() as s:
        assert arena.request_evaluation(s, bid).changed is True
    arena.work_once(sf, W, llm)
    new_calls = llm.calls[before:]
    assert len(new_calls) == 3  # only the 3 missing (batch 2, repeat r) combinations
    from tests.arena.helpers import persona_ids

    assert all(tuple(persona_ids(c["prompt"])) == batch2 for c in new_calls)
    ev = _evaluation(sf, eid)
    assert ev.status == "complete" and _runs(sf, eid) == 90


def test_evaluator_prompt_is_page_text_only(session_factory) -> None:
    sf = session_factory
    bid = live_business(sf)
    llm = FakeArenaLLM()
    _ready_panel(sf, bid, llm)
    with sf() as s:
        arena.request_evaluation(s, bid)
        sv = s.scalar(select(SiteVersion).where(SiteVersion.business_id == bid))
        headline = sv.content_json["hero"]["headline"]
    arena.work_once(sf, W, llm)
    prompt = next(c["prompt"] for c in llm.calls if c["task"] == "persona_evaluation")
    assert prompt.index("PAGE") < prompt.index(headline) < prompt.index("PERSONAS")
    for leak in ('"hero"', "missing_proof", "price_inr", "target_customer", "competitors",
                 "premortem", "<section", "<h1"):  # fmt: skip
        assert leak not in prompt


def test_persist_is_idempotent_and_composite_fk_guards_runs(session_factory) -> None:
    sf = session_factory
    bid = live_business(sf)
    llm = FakeArenaLLM()
    panel = _ready_panel(sf, bid, llm)
    with sf() as s:
        ev = arena.request_evaluation(s, bid).row
        snap = {"id": ev.id, "panel_id": ev.panel_id, "site_version_id": ev.site_version_id}
    one = EvalOut(persona_id="p01", decision="convert", confidence=0.9, objection_type="other",
                  top_objection="", section="hero", missing_info=[], reason="First.")  # fmt: skip
    other = one.model_copy(update={"decision": "bounce", "reason": "Second."})
    assert arena.persist_batch(sf, snap, 0, 0, [one], "m", "r1", W) == 1
    assert arena.persist_batch(sf, snap, 0, 0, [other], "m", "r2", W) == 0
    with sf() as s:
        run = s.scalar(select(PersonaRun).where(PersonaRun.evaluation_id == ev.id))
        assert (run.decision, run.reasoning, run.run_id) == ("convert", "First.", "r1")
        # A run whose site version does not match its evaluation is rejected.
        other_sv = SiteVersion(business_id=bid, version=99, content_json={}, content_hash="x",
                               status="superseded")  # fmt: skip
        s.add(other_sv)
        s.flush()
        s.add(PersonaRun(evaluation_id=ev.id, panel_id=panel.id, site_version_id=other_sv.id,
                         persona_id="p02", run_idx=0, decision="bounce"))  # fmt: skip
        with pytest.raises(IntegrityError):
            s.flush()


def test_one_running_evaluation_per_business_and_no_status_change(session_factory) -> None:
    sf = session_factory
    bid = live_business(sf)
    llm = FakeArenaLLM()
    _ready_panel(sf, bid, llm)
    with sf() as s:
        arena.request_evaluation(s, bid)
        with pytest.raises(arena.InvalidVersion):
            arena.request_evaluation(s, bid, version=7)
    arena.work_once(sf, W, llm)
    with sf() as s:
        assert s.get(Business, bid).status == "LIVE"  # no business status transitions


def test_evaluation_requires_ready_panel(session_factory) -> None:
    bid = live_business(session_factory)
    with session_factory() as s, pytest.raises(arena.PersonasNotReady):
        arena.request_evaluation(s, bid)
    assert uuid.UUID(str(bid))
