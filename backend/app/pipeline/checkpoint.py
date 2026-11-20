"""Mid-stage checkpoints shared by stage handlers (M3 pre-mortem, M4B deploy).

A checkpoint commits part of a stage's work (typically a paid model result or an external side
effect) before the stage finishes, but only while the caller still owns the business.
"""

from collections.abc import Callable
from datetime import timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.config import WORKER_CLAIM_TTL_SECONDS
from app.llm.routing import get_task
from app.llm.structured import MAX_VALIDATION_RETRIES
from app.models import Business, BusinessStatus
from app.pipeline import status
from app.pipeline.runner import current_worker_id


def checkpoint[T](
    session: Session,
    business_id: object,
    expected: BusinessStatus,
    apply: Callable[[Business], T],
    *,
    commit: bool = True,
) -> T:
    """Commit `apply`'s writes only if we still own the business. Returns apply's result.

    Takes the row lock (so no other worker can claim it meanwhile), then requires the status to
    still be `expected` and, when run by a worker, a live lease (LeaseLost / StaleState end the
    stage quietly in the runner, writing nothing). A successful checkpoint extends the lease
    for the next long call. commit=False flushes instead, so the writes commit atomically with
    the runner's following status transition (or not at all).
    """
    locked = status.lock_business(session, business_id)
    if locked.status != expected:
        raise status.StaleState(f"Business {locked.id} is {locked.status}, expected {expected}")
    worker = current_worker_id.get()
    if worker is not None:
        status.assert_lease(session, locked, worker)
        locked.locked_until = func.now() + timedelta(seconds=WORKER_CLAIM_TTL_SECONDS)
    result = apply(locked)
    if commit:
        session.commit()
    else:
        session.flush()
    return result


# Safety margin on top of the model-call budget (scheduling, DB round trips, backoff sleeps).
MODEL_CALL_LEASE_MARGIN_S = 60


def model_call_lease_s(task: str) -> int:
    """Lease needed to cover one run_structured call for `task`.

    Every validation attempt (1 + MAX_VALIDATION_RETRIES repairs) can take up to the task's
    configured timeout, plus a margin. Transport retries of timed-out requests could in theory
    take longer; a lease that ran out would then end the stage quietly (LeaseLost) without
    writing, which is safe. The lease is never shorter than the normal worker TTL.
    """
    spec = get_task(task)
    budget = spec.timeout_s * (MAX_VALIDATION_RETRIES + 1) + MODEL_CALL_LEASE_MARGIN_S
    return int(max(WORKER_CLAIM_TTL_SECONDS, budget))


def hold_lease(
    session: Session, business_id: object, expected: BusinessStatus, seconds: float
) -> None:
    """Before a long external call: verify we still own the business (same checks as a
    checkpoint) and extend the lease to cover `seconds`. Commits only the lease change.
    Raises StaleState / LeaseLost before anything is spent if ownership is already gone."""

    def extend(locked: Business) -> None:
        if current_worker_id.get() is not None:
            locked.locked_until = func.now() + timedelta(seconds=seconds)

    checkpoint(session, business_id, expected, extend)
