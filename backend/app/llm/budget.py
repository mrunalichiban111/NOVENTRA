"""Per-run spend cap with DB-backed reservations (cross-process safe).

Before EVERY real provider attempt (first calls, transport retries, validation repairs)
`LLMGuard.before` reserves the worst-case cost of that attempt; `after` settles it
(writing the llm_usage row in the same short transaction) or releases it. No DB
transaction is ever held open during the network call.

Race safety: Postgres READ COMMITTED lets two concurrent "check then insert" statements
both pass. So the reserve transaction first takes one transaction-scoped advisory lock
keyed by run_id (a single `pg_advisory_xact_lock`, released at commit) and only then runs
the atomic INSERT ... SELECT check, which therefore sees a fresh snapshot.

Crash recovery is expiry only: active reservations older than RESERVATION_TTL_S stop
counting. No cleanup job. Mock mode never reaches this module (cost 0, no cap).
"""

import logging
import math
import uuid
from dataclasses import dataclass
from decimal import ROUND_UP, Decimal

from sqlalchemy import text

from app import config
from app.llm.base import (
    BudgetExceeded,
    InputTooLarge,
    LLMRequest,
    LLMResponse,
    RunIdRequired,
)
from app.llm.pricing import MAX_INPUT_TOKENS, estimate_cost_usd, worst_case_cost_usd
from app.llm.usage import SessionFactory, add_usage, default_session_factory

logger = logging.getLogger(__name__)

RESERVATION_TTL_S = 300
CHARS_PER_TOKEN = 3  # deliberately conservative (real text averages ~4)

_LOCK_SQL = text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))")
_RESERVE_SQL = text(
    """
    INSERT INTO llm_budget_reservations (id, run_id, amount_usd, status, created_at, expires_at)
    SELECT CAST(:id AS uuid), CAST(:run_id AS varchar), CAST(:amount AS numeric), 'active',
           now(), now() + make_interval(secs => CAST(:ttl AS double precision))
    WHERE (
        COALESCE((SELECT SUM(estimated_cost_usd) FROM llm_usage
                  WHERE run_id = CAST(:run_id AS varchar)), 0)
      + COALESCE((SELECT SUM(amount_usd) FROM llm_budget_reservations
                  WHERE run_id = CAST(:run_id AS varchar) AND status = 'active'
                    AND expires_at > now()), 0)
      + CAST(:amount AS numeric)
    ) <= CAST(:cap AS numeric)
    RETURNING id
    """
)


def estimate_input_tokens(request: LLMRequest) -> int:
    chars = len(request.system) + len(request.user) + len(request.repair_feedback or "")
    if request.tool_schema is not None:
        chars += len(str(request.tool_schema)) + len(request.tool_description)
    return math.ceil(chars / CHARS_PER_TOKEN)


def worst_case_usd(request: LLMRequest) -> Decimal:
    """Upper bound for one attempt. Raises PricingMissing (fail closed) or InputTooLarge."""
    est_in = estimate_input_tokens(request)
    if est_in > MAX_INPUT_TOKENS:
        raise InputTooLarge(f"estimated input {est_in} tokens exceeds {MAX_INPUT_TOKENS}")
    cost = worst_case_cost_usd(
        request.model, est_input_tokens=est_in, max_tokens=request.max_tokens
    )
    return Decimal(str(cost)).quantize(Decimal("0.000001"), rounding=ROUND_UP)


@dataclass(frozen=True)
class Reservation:
    id: uuid.UUID
    amount: Decimal


_FINALIZE_SQL = text(
    """
    UPDATE llm_budget_reservations SET status = CAST(:status AS varchar)
    WHERE id = CAST(:id AS uuid) AND status = 'active'
    RETURNING id
    """
)


class LLMGuard:
    """CallGuard that enforces the per-run cap and persists usage. Always needs a run_id."""

    def __init__(
        self,
        *,
        business_id: uuid.UUID | None,
        run_id: str | None,
        session_factory: SessionFactory | None = None,
        provider_name: str = "claude",
        cap_usd: float | None = None,
    ):
        if not run_id or run_id == "-":
            raise RunIdRequired("LLMGuard needs a run_id: real calls are never uncapped")
        self.business_id = business_id
        self.run_id: str = run_id
        self.provider_name = provider_name
        self._factory = session_factory
        self._cap = cap_usd

    @property
    def factory(self) -> SessionFactory:
        if self._factory is None:
            self._factory = default_session_factory()
        return self._factory

    def before(self, request: LLMRequest, call_no: int) -> Reservation:
        amount = worst_case_usd(request)  # fail closed on missing pricing / oversized input
        cap = Decimal(str(self._cap if self._cap is not None else config.PER_RUN_CAP_USD))
        rid = uuid.uuid4()
        with self.factory() as session:
            session.execute(_LOCK_SQL, {"k": self.run_id})
            row = session.execute(
                _RESERVE_SQL,
                {
                    "id": rid,
                    "run_id": self.run_id,
                    "amount": amount,
                    "ttl": RESERVATION_TTL_S,
                    "cap": cap,
                },
            ).first()
            session.commit()  # releases the advisory lock
        if row is None:
            raise BudgetExceeded(
                f"per-run cap ${cap} would be exceeded by worst-case ${amount} for this attempt"
            )
        return Reservation(rid, amount)

    def after(
        self,
        token: object,
        request: LLMRequest,
        call_no: int,
        response: LLMResponse | None,
        status: str,
        error_category: str | None,
    ) -> None:
        """Finalize the reservation exactly once.

        - response known (success, validation failure, refusal): settle at the actual
          estimated cost and persist usage.
        - ambiguous transport outcome (timeout/interruption after sending): the provider may
          have billed us, so settle at the WORST-CASE estimate (counted permanently, with the
          error category recorded; tokens are unknown and stay 0).
        - anything else without a response (not sent / provider rejected): release.
        """
        assert isinstance(token, Reservation)
        ambiguous = response is None and error_category == "transport_ambiguous"
        try:
            with self.factory() as session:
                final = "released" if response is None and not ambiguous else "settled"
                # Compare-and-set on status='active': a second call finds nothing and does nothing.
                if (
                    session.execute(_FINALIZE_SQL, {"id": token.id, "status": final}).first()
                    is None
                ):
                    logger.warning("reservation %s already finalized; skipping", token.id)
                    session.rollback()
                    return
                if response is not None or ambiguous:
                    r = response or LLMResponse(model=request.model)
                    cost = (
                        estimate_cost_usd(
                            request.model,
                            input_tokens=r.input_tokens,
                            output_tokens=r.output_tokens,
                            cache_read_tokens=r.cache_read_tokens,
                            cache_write_tokens=r.cache_write_tokens,
                        )
                        if response is not None
                        else token.amount
                    )
                    add_usage(
                        session,
                        business_id=self.business_id,
                        run_id=self.run_id,
                        task=request.task,
                        provider=self.provider_name,
                        model=request.model,
                        attempt=call_no,
                        input_tokens=r.input_tokens,
                        output_tokens=r.output_tokens,
                        cache_read_tokens=r.cache_read_tokens,
                        cache_write_tokens=r.cache_write_tokens,
                        estimated_cost_usd=cost,
                        latency_ms=r.latency_ms,
                        status=status,
                        error_category=error_category,
                    )
                session.commit()
        except Exception:
            # Never mask the model result; an unfinalized reservation simply expires.
            logger.exception("failed to settle llm usage task=%s call=%d", request.task, call_no)
