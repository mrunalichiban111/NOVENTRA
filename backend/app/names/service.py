"""Name generation, listing, availability and selection (M4A: FR-BR-1..5, FR-GT-2).

The routes in app/api/names.py are thin wrappers; every rule lives here. Callers own
commit/rollback except where noted. `select_candidate` is the only code that changes a claim.
"""

import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import config
from app.agents import names as names_agent
from app.agents.premortem import RunLLM
from app.domains import availability as av
from app.llm.structured import run_structured
from app.models import AuditLog, Business, BusinessStatus, DomainCandidate, DomainCheck
from app.names import repository
from app.pipeline import analysis, status
from app.pipeline.status import Actor

logger = logging.getLogger(__name__)
S = BusinessStatus

PASSING_VERDICTS = frozenset({"go", "go_with_changes"})
MAX_GENERATIONS = 3
GENERATED = "names.generated"
AGENT = Actor("agent:business_names")


class NamesError(Exception):
    http_status = 400
    code = "names_error"


class NotFound(NamesError):
    http_status, code = 404, "not_found"


class NotEligible(NamesError):
    http_status, code = 409, "not_eligible"


class GenerationLimit(NamesError):
    http_status, code = 409, "generation_limit"


class IdempotencyMismatch(NamesError):
    http_status, code = 422, "idempotency_key_reused"


class InvalidCandidate(NamesError):
    http_status, code = 422, "invalid_candidate"


class NotInTopFive(NamesError):
    http_status, code = 422, "not_in_top_five"


class AlreadySelected(NamesError):
    http_status, code = 409, "already_selected"


class ClaimConflict(NamesError):
    http_status, code = 409, "slug_claimed"


class SubdomainNotConfigured(NamesError):
    http_status, code = 503, "subdomain_not_configured"


class GenerationFailed(NamesError):
    http_status, code = 502, "generation_failed"


# --- helpers ---------------------------------------------------------------------------


def _business(session: Session, business_id: uuid.UUID, *, lock: bool = False) -> Business:
    if lock:
        try:
            return status.lock_business(session, business_id)
        except status.BusinessNotFound:
            raise NotFound(f"Business {business_id} not found") from None
    business = session.get(Business, business_id)
    if business is None:
        raise NotFound(f"Business {business_id} not found")
    return business


def check_verdict(session: Session, business: Business) -> str:
    """The persisted pre-launch verdict must pass; GATED alone is not proof."""
    card = analysis.latest_scorecard(session, business.id)
    if card is None:
        raise NotEligible("No persisted verdict for this business; names are not available.")
    if card.verdict not in PASSING_VERDICTS:
        raise NotEligible(
            f"Verdict is {card.verdict!r}; names need a Go or Go with changes verdict."
        )
    return card.verdict


def _require_gated(session: Session, business: Business) -> None:
    if business.status != S.GATED:
        raise NotEligible(f"Business is {business.status}; names can be generated only in GATED.")
    check_verdict(session, business)


def generation_count(session: Session, business_id: uuid.UUID) -> int:
    return session.scalar(
        select(func.count())
        .select_from(AuditLog)
        .where(AuditLog.business_id == business_id, AuditLog.action == GENERATED)
    )


def fingerprint(business_id: uuid.UUID, body: dict[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{business_id}|names.generate|{canonical}".encode()).hexdigest()


def _idem_key(key: str) -> str:
    # Global namespace: a key reused for another business or body is a mismatch.
    return f"names.generate:{key}"


def _replay(session: Session, key: str, fp: str) -> AuditLog | None:
    row = session.scalar(select(AuditLog).where(AuditLog.idempotency_key == _idem_key(key)))
    if row is not None and row.details.get("fingerprint") != fp:
        raise IdempotencyMismatch("Idempotency-Key was already used for a different request.")
    return row


# --- generation ------------------------------------------------------------------------


@dataclass
class GenerateOutcome:
    run_id: str
    replayed: bool
    generation: int


def generate(
    session: Session,
    business_id: uuid.UUID,
    *,
    idempotency_key: str | None,
    body: dict[str, Any],
    run_llm: RunLLM = run_structured,
) -> GenerateOutcome:
    """Generate, rank and persist candidates. Commits on success.

    Idempotency: with a key, a previous success with the same fingerprint is replayed (no model
    call); a different fingerprint is 422. The check runs before the model call and again under
    the business lock, and the audit row (holding the key) commits atomically with the rows.
    """
    business = _business(session, business_id)
    fp = fingerprint(business_id, body)
    if idempotency_key:
        prior = _replay(session, idempotency_key, fp)
        if prior is not None:
            return GenerateOutcome(
                prior.version_ref or "", True, prior.details.get("generation", 0)
            )
    _require_gated(session, business)
    if generation_count(session, business_id) >= MAX_GENERATIONS:
        raise GenerationLimit(f"Names were already generated {MAX_GENERATIONS} times.")
    if not business.premortem_json:
        raise NotEligible("This business has no stored pre-mortem to name from.")

    run_id = f"names-{uuid.uuid4().hex[:12]}"
    session.expunge(business)  # keep the loaded copy; it is only read by the agent
    session.rollback()  # hold no transaction open across the model call
    try:
        records = names_agent.generate_names(business, run_id=run_id, run_llm=run_llm)
    except Exception as exc:
        logger.warning("business_id=%s run_id=%s names failed: %s", business_id, run_id, exc)
        raise GenerationFailed("Name generation failed; please try again.") from None

    locked = _business(session, business_id, lock=True)
    if idempotency_key:
        prior = _replay(session, idempotency_key, fp)
        if prior is not None:  # a concurrent request with the same key won
            session.rollback()
            return GenerateOutcome(
                prior.version_ref or "", True, prior.details.get("generation", 0)
            )
    _require_gated(session, locked)
    count = generation_count(session, business_id)
    if count >= MAX_GENERATIONS:
        raise GenerationLimit(f"Names were already generated {MAX_GENERATIONS} times.")

    valid = sum(r.valid for r in records)
    inserted = status.record_event(
        session,
        locked,
        actor=AGENT,
        action=GENERATED,
        reason=f"Generated {len(records)} name candidates ({valid} valid)",
        version_ref=run_id,
        details={
            "run_id": run_id,
            "generation": count + 1,
            "candidates": len(records),
            "valid": valid,
            "fingerprint": fp,
            "idempotency_key": idempotency_key,
        },
        key=_idem_key(idempotency_key) if idempotency_key else f"{business_id}:names:{run_id}",
    )
    if not inserted:  # unreachable without a key; with one, the lock above serialises
        session.rollback()
        prior = _replay(session, idempotency_key or "", fp)
        return GenerateOutcome(run_id, True, prior.details.get("generation", 0) if prior else 0)
    repository.replace_unselected(session, business_id, run_id, records)
    session.commit()
    return GenerateOutcome(run_id, False, count + 1)


# --- listing ---------------------------------------------------------------------------


def subdomain_host(slug: str | None) -> str | None:
    return f"{slug}.{config.BASE_DOMAIN}" if slug and config.BASE_DOMAIN else None


def claimed_elsewhere(session: Session, business_id: uuid.UUID, slugs: list[str]) -> set[str]:
    if not slugs:
        return set()
    return set(
        session.scalars(
            select(DomainCandidate.slug).where(
                DomainCandidate.selected.is_(True),
                DomainCandidate.business_id != business_id,
                DomainCandidate.slug.in_(slugs),
            )
        )
    )


def list_names(session: Session, business_id: uuid.UUID) -> tuple[Business, list, set[str]]:
    business = _business(session, business_id)
    rows = repository.list_candidates(session, business_id)
    taken = claimed_elsewhere(session, business_id, [r.slug for r in rows if r.valid and r.slug])
    return business, rows, taken


# --- real-domain availability ----------------------------------------------------------


def _candidate(session: Session, business_id: uuid.UUID, candidate_id: uuid.UUID):  # noqa: ANN202
    cand = session.get(DomainCandidate, candidate_id)
    if cand is None or cand.business_id != business_id:
        raise NotFound(f"Candidate {candidate_id} not found for this business")
    return cand


def check_availability(
    session: Session,
    business_id: uuid.UUID,
    candidate_id: uuid.UUID,
    *,
    provider: av.DomainAvailabilityProvider | None = None,
    cache: av.AvailabilityCache | None = None,
) -> tuple[DomainCandidate, list[av.DomainResult]]:
    """Check the configured TLDs for a top-five valid candidate; persist and audit. Commits."""
    _business(session, business_id)
    cand = _candidate(session, business_id, candidate_id)
    if not cand.valid or not cand.slug:
        raise InvalidCandidate(f"Candidate is invalid ({cand.invalid_reason}).")
    if not cand.selected and (cand.rank is None or cand.rank > av.TOP_N):
        raise NotInTopFive(f"Only the top {av.TOP_N} ranked candidates can be checked.")
    slug, cid = cand.slug, cand.id
    session.rollback()  # no transaction open across network calls

    results = av.check_domains([f"{slug}{tld}" for tld in config.GODADDY_TLDS], provider, cache)

    for r in results:
        values = {
            "status": r.status.value,
            "reason": r.reason,
            "raw_price": r.raw_price,
            "currency": r.currency,
            "definitive": r.definitive,
            "checked_at": func.now(),
        }
        session.execute(
            pg_insert(DomainCheck)
            .values(id=uuid.uuid4(), candidate_id=cid, tld=r.tld, **values)
            .on_conflict_do_update(constraint="uq_domain_checks_candidate_tld", set_=values)
        )
    business = _business(session, business_id)
    status.record_event(
        session,
        business,
        actor=Actor.user(),
        action="names.availability_checked",
        reason=f"Checked {len(results)} domains for {slug}",
        details={
            "candidate_id": str(cid),
            "slug": slug,
            "results": {r.domain: r.status.value for r in results},
            "provider": (provider or av.get_provider()).name,
        },
    )
    session.commit()
    return _candidate(session, business_id, cid), results


# --- selection (the only path that changes a claim) ------------------------------------


@dataclass
class SelectOutcome:
    candidate: DomainCandidate
    changed: bool
    replaced_slug: str | None


def select_candidate(
    session: Session, business_id: uuid.UUID, candidate_id: uuid.UUID, *, replace: bool
) -> SelectOutcome:
    """Claim `<slug>.<BASE_DOMAIN>` for the business in one transaction. Commits on success.

    Same candidate again: no-op. Another one already selected: 409 unless replace=True, in
    which case the old claim is released and the new one taken in the same transaction; a
    uniqueness conflict rolls everything back, so the old claim stays intact.
    GATED -> DOMAIN_PENDING on the first selection; a replacement in DOMAIN_PENDING is audited
    without another transition.
    """
    business = _business(session, business_id, lock=True)
    cand = _candidate(session, business_id, candidate_id)
    if not config.BASE_DOMAIN:
        raise SubdomainNotConfigured("BASE_DOMAIN is not set; subdomains cannot be claimed yet.")
    if not cand.valid or not cand.slug:
        raise InvalidCandidate(f"Candidate is invalid ({cand.invalid_reason}).")
    if business.status not in (S.GATED, S.DOMAIN_PENDING):
        raise NotEligible(f"Business is {business.status}; a name can't be selected now.")
    check_verdict(session, business)
    if cand.selected:
        return SelectOutcome(cand, False, None)

    current = session.scalar(
        select(DomainCandidate).where(
            DomainCandidate.business_id == business_id, DomainCandidate.selected.is_(True)
        )
    )
    if current is not None and not replace:
        raise AlreadySelected(
            f"{current.slug!r} is already selected; pass replace=true to change it."
        )

    previous = current.slug if current is not None else None
    host = subdomain_host(cand.slug)
    try:
        if current is not None:
            current.selected = False
            business.domain = None
            session.flush()
        cand.selected = True
        business.domain = host
        session.flush()
    except IntegrityError:
        session.rollback()
        raise ClaimConflict(f"{cand.slug!r} is already claimed by another business.") from None

    action = "names.replaced" if previous else "names.selected"
    reason = f"Claimed {host}" + (f" (replacing {previous})" if previous else "")
    if business.status == S.GATED:
        status.transition(
            session,
            business_id,
            S.DOMAIN_PENDING,
            actor=Actor.user(),
            action=action,
            reason=reason,
            version_ref=cand.run_id,
            expected_from=S.GATED,
        )
    else:
        status.record_event(
            session,
            business,
            actor=Actor.user(),
            action=action,
            reason=reason,
            version_ref=cand.run_id,
            details={"slug": cand.slug, "host": host, "previous_slug": previous},
        )
    session.commit()
    return SelectOutcome(cand, True, previous)
