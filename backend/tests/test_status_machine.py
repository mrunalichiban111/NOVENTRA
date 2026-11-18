import itertools
import threading
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from app.models import AuditLog, Business, BusinessStatus
from app.pipeline import status as sm
from app.pipeline.status import (
    PIPELINE_ORDER,
    TRANSITIONS,
    Actor,
    InvalidTransition,
    RetryLimitExceeded,
    RetryNotAllowed,
    StaleState,
)

S = BusinessStatus
USER = Actor.user()

VALID = [(a, b) for a, targets in TRANSITIONS.items() for b in targets]
INVALID = [
    (a, b)
    for a, b in itertools.product(S, S)
    if b not in TRANSITIONS[a] and a != S.FAILED and b != S.FAILED
]


def make(session: Session, status: BusinessStatus = S.INTAKE, **kw) -> Business:
    b = Business(idea_text="Idea one. Idea two.", status=status.value, **kw)
    session.add(b)
    session.flush()
    sm.record_creation(session, b, USER)
    session.commit()
    return b


def audit(session: Session, business_id: uuid.UUID) -> list[AuditLog]:
    return list(
        session.scalars(
            select(AuditLog).where(AuditLog.business_id == business_id).order_by(AuditLog.id)
        )
    )


def test_map_covers_all_statuses_and_terminals_are_closed() -> None:
    assert set(TRANSITIONS) == set(S)
    assert TRANSITIONS[S.DONE] == frozenset() and TRANSITIONS[S.REJECTED] == frozenset()


def test_happy_path_edges_exist() -> None:
    for a, b in zip(PIPELINE_ORDER, PIPELINE_ORDER[1:], strict=False):
        assert b in TRANSITIONS[a]


@pytest.mark.parametrize(("a", "b"), [(a, b) for a, b in VALID if a != S.FAILED and b != S.FAILED])
def test_every_valid_edge_is_applied_and_audited(
    session: Session, a: BusinessStatus, b: BusinessStatus
) -> None:
    biz = make(session, a)
    sm.transition(
        session, biz.id, b, actor=USER, action="test.step", reason="why", version_ref="v1"
    )
    session.commit()
    session.refresh(biz)
    assert biz.status == b
    last = audit(session, biz.id)[-1]
    assert (last.actor, last.action, last.reason, last.version_ref) == (
        "user",
        "test.step",
        "why",
        "v1",
    )
    assert (last.from_status, last.to_status) == (a, b)
    assert last.ts is not None


@pytest.mark.parametrize(("a", "b"), INVALID)
def test_every_invalid_edge_is_rejected_without_side_effects(
    session: Session, a: BusinessStatus, b: BusinessStatus
) -> None:
    biz = make(session, a)
    before = len(audit(session, biz.id))
    with pytest.raises(InvalidTransition):
        sm.transition(session, biz.id, b, actor=USER, action="x")
    session.rollback()
    session.refresh(biz)
    assert biz.status == a
    assert len(audit(session, biz.id)) == before


def test_transition_to_failed_must_use_fail(session: Session) -> None:
    biz = make(session)
    with pytest.raises(InvalidTransition):
        sm.transition(session, biz.id, S.FAILED, actor=USER, action="x")


def test_unknown_business(session: Session) -> None:
    with pytest.raises(sm.BusinessNotFound):
        sm.transition(session, uuid.uuid4(), S.PREMORTEM, actor=USER, action="x")


def test_creation_audit_entry(session: Session) -> None:
    biz = make(session)
    (entry,) = audit(session, biz.id)
    assert (entry.action, entry.from_status, entry.to_status, entry.actor) == (
        "business.created", None, "INTAKE", "user",
    )  # fmt: skip


def test_stale_expected_from_is_rejected_and_not_duplicated(session: Session) -> None:
    biz = make(session)
    sm.transition(session, biz.id, S.PREMORTEM, actor=USER, action="a", expected_from=S.INTAKE)
    session.commit()
    with pytest.raises(StaleState):  # a second worker re-running the same completed stage
        sm.transition(session, biz.id, S.PREMORTEM, actor=USER, action="a", expected_from=S.INTAKE)
    session.rollback()
    assert len(audit(session, biz.id)) == 2  # created + one transition


def test_fail_records_stage_error_and_audit(session: Session) -> None:
    biz = make(session, S.DEPLOYING)
    sm.fail(session, biz.id, actor=Actor.worker("w1"), error="boom")
    session.commit()
    session.refresh(biz)
    assert (biz.status, biz.failed_stage, biz.last_error, biz.attempts) == (
        "FAILED",
        "DEPLOYING",
        "boom",
        1,
    )
    last = audit(session, biz.id)[-1]
    assert (last.action, last.reason, last.from_status, last.to_status) == (
        "pipeline.failed", "boom", "DEPLOYING", "FAILED",
    )  # fmt: skip


@pytest.mark.parametrize("terminal", [S.DONE, S.REJECTED, S.FAILED])
def test_cannot_fail_terminal_or_failed(session: Session, terminal: BusinessStatus) -> None:
    biz = make(session, terminal)
    with pytest.raises(InvalidTransition):
        sm.fail(session, biz.id, actor=USER, error="x")


def test_retry_returns_to_failed_stage_and_is_audited(session: Session) -> None:
    biz = make(session, S.GATED)
    sm.fail(session, biz.id, actor=USER, error="x")
    session.commit()
    sm.retry(session, biz.id, actor=USER)
    session.commit()
    session.refresh(biz)
    assert (biz.status, biz.failed_stage, biz.last_error) == ("GATED", None, None)
    assert biz.attempts == 1
    last = audit(session, biz.id)[-1]
    assert (last.action, last.from_status, last.to_status) == ("pipeline.retry", "FAILED", "GATED")


def test_retry_requires_failed(session: Session) -> None:
    biz = make(session)
    with pytest.raises(RetryNotAllowed):
        sm.retry(session, biz.id, actor=USER)


def test_generic_transition_cannot_leave_failed(session: Session) -> None:
    biz = make(session, S.LIVE)
    sm.fail(session, biz.id, actor=USER, error="x")
    session.commit()
    with pytest.raises(InvalidTransition):
        sm.transition(session, biz.id, S.TESTING, actor=USER, action="x")


def test_retry_limit_is_enforced(session: Session) -> None:
    biz = make(session)
    for _ in range(3):
        sm.fail(session, biz.id, actor=USER, error="x")
        session.commit()
        if biz.attempts < 3:
            sm.retry(session, biz.id, actor=USER, max_attempts=3)
            session.commit()
    with pytest.raises(RetryLimitExceeded):
        sm.retry(session, biz.id, actor=USER, max_attempts=3)
    session.rollback()
    session.refresh(biz)
    assert (biz.status, biz.attempts) == ("FAILED", 3)


def test_audit_rolls_back_with_status_change(session: Session) -> None:
    biz = make(session)
    sm.transition(session, biz.id, S.PREMORTEM, actor=USER, action="a")
    session.rollback()  # e.g. a later error in the same transaction
    session.refresh(biz)
    assert biz.status == "INTAKE"
    assert len(audit(session, biz.id)) == 1


def test_concurrent_transitions_apply_once(
    session: Session, session_factory: sessionmaker[Session]
) -> None:
    biz = make(session)
    results: list[str] = []

    def run() -> None:
        with session_factory() as s:
            try:
                sm.transition(
                    s, biz.id, S.PREMORTEM, actor=USER, action="a", expected_from=S.INTAKE
                )
                s.commit()
                results.append("ok")
            except StaleState:
                s.rollback()
                results.append("stale")

    threads = [threading.Thread(target=run) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["ok"] + ["stale"] * 5
    assert (
        session.scalar(
            select(func.count()).select_from(AuditLog).where(AuditLog.to_status == "PREMORTEM")
        )
        == 1
    )
