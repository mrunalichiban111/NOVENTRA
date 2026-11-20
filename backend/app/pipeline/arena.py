"""Persona arena jobs (M5: FR-AR-1..4, FR-AR-6, FR-AR-7, SRS 6). All results are SIMULATED.

The business stays LIVE throughout: progress lives in persona_panels.status,
persona_evaluations.status and persona_runs counts, never in a business status transition.

Jobs are claimed like pipeline businesses (FOR UPDATE SKIP LOCKED plus a lease) and each worker
execution gets a fresh run_id, so the existing $2 per-run cap applies per execution.

Panel: pending -> generating (committed BEFORE the paid call) -> ready | failed. A `generating`
panel whose lease expired means a paid call may have happened without its result being stored:
it becomes `failed` and is never silently paid for again (an explicit generate re-queues it).

Evaluation: 3 batches x 10 personas x 3 repeats = 9 independent Haiku calls, at most 5 at a time.
Every successful call commits its runs immediately (INSERT ... ON CONFLICT DO NOTHING on
(evaluation_id, persona_id, run_idx)), so a failed call never erases successes and a retry only
runs the persona/repeat combinations that are still missing. D and SE are computed in Python
only when every expected run exists; otherwise the evaluation is `incomplete` and has no D.
"""

import logging
import uuid
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import func, or_, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session, sessionmaker

from app import config
from app.agents import personas as agent
from app.agents.premortem import RunLLM
from app.deploy import service
from app.deploy.render import render_site
from app.deploy.site_model import site_links
from app.deploy.visible_text import extract_visible_text, page_text
from app.finance.scoring import RUBRIC_VERSION, demand_score, display_score
from app.llm.base import BudgetExceeded, ConfigError, LLMError
from app.llm.schemas import PersonaEvaluation, WebsiteContent
from app.llm.structured import run_structured
from app.models import (
    Business,
    PersonaPanel,
    PersonaRun,
    SiteVersion,
)
from app.models import (
    PersonaEvaluation as Evaluation,
)
from app.models import SiteVersionStatus as V
from app.pipeline import status
from app.pipeline.checkpoint import model_call_lease_s
from app.pipeline.runner import current_run_id
from app.pipeline.status import Actor

logger = logging.getLogger(__name__)

BATCH_SIZE = 10
REPEATS = 3


def expected_runs_for(panel: Any) -> int:
    """Evaluations expected for a panel: actual persona count x repeats (never a fixed 90)."""
    return len(panel.personas_json or []) * REPEATS


MAX_WORKERS = 5  # concurrency cap for persona calls
EXTRA_ROUNDS = 2  # retries of still-missing combinations inside one job
EVAL_LEASE_S = 600
SIMULATED_LABEL = "Simulated results, not market research"
# Site versions whose immutable release may be evaluated: the live one, earlier live ones, and
# an M6 draft that is built but not activated.
EVALUABLE = (V.DEPLOYED, V.SUPERSEDED, V.BUILT, V.ROLLED_BACK)

SessionFactory = sessionmaker[Session]


class ArenaError(Exception):
    code = "arena_error"


class NoLiveVersion(ArenaError):
    code = "no_live_version"


class PersonasNotReady(ArenaError):
    code = "personas_not_ready"


class EvaluationInProgress(ArenaError):
    code = "evaluation_in_progress"


class InvalidVersion(ArenaError):
    code = "invalid_version"


class ReleaseUnavailable(ArenaError):
    code = "release_unavailable"


def _actor_user() -> Actor:
    return Actor.user()


def _new_run_id() -> str:
    return uuid.uuid4().hex[:12]


# --- API side: requests ---------------------------------------------------------------------


@dataclass(frozen=True)
class Requested[T]:
    row: T
    changed: bool


def get_panel(session: Session, business_id: uuid.UUID) -> PersonaPanel | None:
    return session.scalar(
        select(PersonaPanel)
        .where(PersonaPanel.business_id == business_id)
        .execution_options(populate_existing=True)
    )


def request_panel(session: Session, business_id: uuid.UUID) -> Requested[PersonaPanel]:
    """Create (or re-queue a failed) panel under the business row lock. Commits."""
    business = status.lock_business(session, business_id)
    if service.deployed_version(session, business_id) is None:
        session.rollback()
        raise NoLiveVersion("The persona arena needs a live website first.")
    panel = get_panel(session, business_id)
    if panel is not None and panel.status != "failed":
        session.commit()  # nothing written; releases the row lock without expiring `panel`
        return Requested(panel, False)
    if panel is None:
        panel = PersonaPanel(business_id=business_id, status="pending")
        session.add(panel)
        session.flush()
        action, reason = "personas.requested", "Simulated persona panel requested"
    else:
        panel.status, panel.error = "pending", None
        action, reason = "personas.retry", "Persona panel generation retried (new paid call)"
    status.record_event(
        session,
        business,
        actor=_actor_user(),
        action=action,
        reason=reason,
        details={"panel_id": str(panel.id), "attempt": panel.generation_attempt},
    )
    session.commit()
    return Requested(panel, True)


def _evaluable_version(
    session: Session, business_id: uuid.UUID, version: int | None
) -> SiteVersion:
    if version is None:
        sv = service.deployed_version(session, business_id)
        if sv is None:
            raise NoLiveVersion("There is no live website version to evaluate.")
        return sv
    sv = session.scalar(
        select(SiteVersion).where(
            SiteVersion.business_id == business_id, SiteVersion.version == version
        )
    )
    if sv is None or sv.status not in EVALUABLE or not sv.artifact_hash:
        raise InvalidVersion(f"Site v{version} has no built release that can be evaluated.")
    return sv


def get_evaluation(
    session: Session, business_id: uuid.UUID, version: int | None
) -> tuple[Evaluation, SiteVersion] | None:
    """The evaluation for a version (default: the live one, else the latest evaluated)."""
    q = (
        select(Evaluation, SiteVersion)
        .join(SiteVersion, SiteVersion.id == Evaluation.site_version_id)
        .where(Evaluation.business_id == business_id)
        .execution_options(populate_existing=True)
    )
    if version is not None:
        q = q.where(SiteVersion.version == version)
    else:
        live = service.deployed_version(session, business_id)
        if live is not None:
            row = session.execute(q.where(SiteVersion.id == live.id)).first()
            if row is not None:
                return row[0], row[1]
        q = q.order_by(Evaluation.created_at.desc())
    row = session.execute(q.limit(1)).first()
    return (row[0], row[1]) if row else None


def request_evaluation(
    session: Session, business_id: uuid.UUID, version: int | None = None
) -> Requested[Evaluation]:
    """Queue an evaluation of one version (idempotent). An `incomplete` evaluation is re-queued
    and will only run the missing combinations; a complete one is returned unchanged. Commits."""
    business = status.lock_business(session, business_id)
    try:
        panel = get_panel(session, business_id)
        if panel is None or panel.status != "ready":
            raise PersonasNotReady("Generate the simulated persona panel first.")
        sv = _evaluable_version(session, business_id, version)
        existing = session.scalar(
            select(Evaluation)
            .where(Evaluation.panel_id == panel.id, Evaluation.site_version_id == sv.id)
            .with_for_update()
        )
        running_other = session.scalar(
            select(Evaluation.id).where(
                Evaluation.business_id == business_id,
                Evaluation.status.in_(["queued", "running"]),
                Evaluation.site_version_id != sv.id,
            )
        )
        if existing is not None and existing.status in ("queued", "running", "complete"):
            session.commit()  # nothing written; releases the locks without expiring `existing`
            return Requested(existing, False)
        if running_other is not None:
            raise EvaluationInProgress("Another version is being evaluated; wait for it to finish.")
    except ArenaError:
        session.rollback()
        raise
    if existing is None:
        existing = Evaluation(
            business_id=business_id,
            panel_id=panel.id,
            site_version_id=sv.id,
            status="queued",
            expected_runs=expected_runs_for(panel),
        )
        session.add(existing)
        session.flush()
        action = "arena.evaluation_requested"
    else:
        existing.status, existing.error, existing.error_code = "queued", None, None
        action = "arena.evaluation_retry_missing"
    status.record_event(
        session,
        business,
        actor=_actor_user(),
        action=action,
        reason=f"Simulated persona evaluation of site v{sv.version}",
        version_ref=f"site-v{sv.version}",
        details={"evaluation_id": str(existing.id)},
    )
    session.commit()
    return Requested(existing, True)


# --- worker side: claims --------------------------------------------------------------------


def _claim(session: Session, model: Any, statuses: list[str], worker_id: str, ttl: int) -> Any:
    candidate = (
        select(model.id)
        .where(
            model.status.in_(statuses),
            or_(model.locked_until.is_(None), model.locked_until < func.now()),
        )
        .order_by(model.created_at, model.id)
        .limit(1)
        .with_for_update(skip_locked=True)
        .scalar_subquery()
    )
    claimed = session.execute(
        update(model)
        .where(model.id == candidate)
        .values(locked_by=worker_id, locked_until=func.now() + timedelta(seconds=ttl))
        .returning(model.id)
        .execution_options(synchronize_session=False)
    ).scalar_one_or_none()
    session.commit()
    return claimed


def claim_panel(session: Session, worker_id: str) -> uuid.UUID | None:
    return _claim(session, PersonaPanel, ["pending", "generating"], worker_id, 60)


def claim_evaluation(session: Session, worker_id: str) -> uuid.UUID | None:
    return _claim(session, Evaluation, ["queued", "running"], worker_id, EVAL_LEASE_S)


def _owned[T](session: Session, model: type[T], row_id: uuid.UUID, worker_id: str) -> T | None:
    row = session.get(model, row_id, with_for_update=True, populate_existing=True)
    if row is None or row.locked_by != worker_id:  # type: ignore[attr-defined]
        return None
    return row


def _audit(session: Session, business_id: uuid.UUID, worker_id: str, action: str, reason: str,
           key: str | None = None, **details: Any) -> None:  # fmt: skip
    business = session.get(Business, business_id)
    if business is not None:
        status.record_event(
            session, business, actor=Actor.worker(worker_id), action=action, reason=reason,
            details=details, key=key,
        )  # fmt: skip


# --- panel job ------------------------------------------------------------------------------


def run_panel_job(
    factory: SessionFactory, panel_id: uuid.UUID, worker_id: str, run_llm: RunLLM = run_structured
) -> None:
    run_id = _new_run_id()
    token = current_run_id.set(run_id)
    try:
        with factory() as s:
            panel = _owned(s, PersonaPanel, panel_id, worker_id)
            if panel is None:
                return
            if panel.status == "generating":  # previous worker died mid-call
                panel.status = "failed"
                panel.error = (
                    "Panel generation was interrupted (a paid call may have happened). "
                    "Generate again to retry."
                )
                panel.locked_by = panel.locked_until = None
                _audit(s, panel.business_id, worker_id, "personas.abandoned", panel.error,
                       run_id=panel.generation_run_id)  # fmt: skip
                s.commit()
                return
            if panel.status != "pending":
                panel.locked_by = panel.locked_until = None
                s.commit()
                return
            business = s.get(Business, panel.business_id)
            panel.status = "generating"
            panel.generation_run_id = run_id
            panel.generation_attempt += 1
            panel.locked_until = func.now() + timedelta(
                seconds=model_call_lease_s(agent.PANEL_TASK)
            )
            s.commit()  # committed BEFORE the paid call
            s.refresh(business)
            s.expunge(business)
        try:
            personas, model = agent.generate_panel(business, run_id=run_id, run_llm=run_llm)
        except Exception as exc:
            logger.warning("panel %s generation failed: %s", panel_id, exc)
            with factory() as s:
                panel = _owned(s, PersonaPanel, panel_id, worker_id)
                if panel is not None:
                    panel.status = "failed"
                    panel.error = _public_error(exc)
                    panel.locked_by = panel.locked_until = None
                    _audit(s, panel.business_id, worker_id, "personas.failed", panel.error,
                           run_id=run_id)  # fmt: skip
                    s.commit()
            return
        with factory() as s:
            panel = _owned(s, PersonaPanel, panel_id, worker_id)
            if panel is None or panel.status != "generating":
                logger.warning("panel %s lost its lease; result discarded", panel_id)
                return
            panel.personas_json = personas
            panel.model = model
            panel.prompt_version = agent.prompt_version(agent.PANEL_TASK)
            panel.status = "ready"
            panel.error = None
            panel.locked_by = panel.locked_until = None
            _audit(s, panel.business_id, worker_id, "personas.generated",
                   f"Simulated panel of {len(personas)} personas generated",
                   key=f"{panel.business_id}:personas:{panel.id}:ready", run_id=run_id)  # fmt: skip
            s.commit()
    finally:
        current_run_id.reset(token)


# --- evaluation job -------------------------------------------------------------------------


def release_html(session: Session, business: Business, sv: SiteVersion) -> str:
    """The exact published HTML of a release: re-rendered deterministically and proven
    byte-identical by the artifact hash recorded when it was built."""
    try:
        claim = service.load_claim(session, business)
        content = WebsiteContent.model_validate(sv.content_json)
        links = site_links(config.WAITLIST_POST_URL, config.ABUSE_REPORT_EMAIL)
        site = render_site(
            content, business_name=claim.name, host=claim.host,
            price_guess=business.price_guess, links=links,
        )  # fmt: skip
    except Exception as exc:
        raise ReleaseUnavailable(f"Release v{sv.version} could not be rendered: {exc}") from exc
    if not sv.artifact_hash or site.artifact_hash != sv.artifact_hash:
        raise ReleaseUnavailable(
            f"Release v{sv.version} no longer matches its recorded artifact (template or site "
            "settings changed); it cannot be evaluated exactly."
        )
    return site.files["index.html"].decode("utf-8")


def missing_combinations(
    session: Session, evaluation_id: uuid.UUID, personas: list[dict[str, Any]]
) -> dict[tuple[int, int], list[dict[str, Any]]]:
    """(batch_index, repeat) -> personas of that batch whose run for that repeat is missing."""
    done = set(
        session.execute(
            select(PersonaRun.persona_id, PersonaRun.run_idx).where(
                PersonaRun.evaluation_id == evaluation_id
            )
        ).all()
    )
    out: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for b in range(0, len(personas), BATCH_SIZE):
        batch = personas[b : b + BATCH_SIZE]
        for r in range(REPEATS):
            todo = [p for p in batch if (p["id"], r) not in done]
            if todo:
                out[(b // BATCH_SIZE, r)] = todo
    return out


def persist_batch(
    factory: SessionFactory,
    evaluation: dict[str, Any],
    batch_index: int,
    repeat: int,
    results: list[PersonaEvaluation],
    model: str,
    run_id: str,
    worker_id: str,
) -> int:
    """Commit one call's runs immediately (own transaction). Idempotent; returns rows inserted."""
    if not results:
        return 0
    rows = [
        {
            "id": uuid.uuid4(),
            "evaluation_id": evaluation["id"],
            "panel_id": evaluation["panel_id"],
            "site_version_id": evaluation["site_version_id"],
            "persona_id": ev.persona_id,
            "run_idx": repeat,
            "batch_index": batch_index,
            "decision": ev.decision,
            "confidence": Decimal(str(ev.confidence)).quantize(Decimal("0.001"), ROUND_HALF_UP),
            "objection_type": ev.objection_type,
            "objection": ev.top_objection or None,
            "section": ev.section,
            "missing_info": list(ev.missing_info),
            "reasoning": ev.reason,
            "model": model,
            "run_id": run_id,
        }
        for ev in results
    ]
    stmt = (
        pg_insert(PersonaRun)
        .values(rows)
        .on_conflict_do_nothing(constraint="uq_persona_runs_eval_persona_repeat")
        .returning(PersonaRun.id)
    )
    with factory() as s:
        inserted = len(s.execute(stmt).all())
        s.execute(
            update(Evaluation)
            .where(Evaluation.id == evaluation["id"], Evaluation.locked_by == worker_id)
            .values(locked_until=func.now() + timedelta(seconds=EVAL_LEASE_S))
        )
        s.commit()
    return inserted


def _error_code(exc: BaseException) -> str:
    if isinstance(exc, BudgetExceeded):
        return "budget_exceeded"
    if isinstance(exc, (agent.EvaluatorMisconfigured, ConfigError)):
        return "evaluator_misconfigured"
    if isinstance(exc, ReleaseUnavailable):
        return "release_unavailable"
    return "evaluator_unavailable"


_FATAL = ("budget_exceeded", "evaluator_misconfigured", "release_unavailable")


def _public_error(exc: BaseException) -> str:
    """Short, safe message (no prompts, no keys)."""
    if isinstance(exc, LLMError):
        return f"{exc.category}: {str(exc)[:200]}"
    return f"{type(exc).__name__}: {str(exc)[:200]}"


def run_evaluation_job(
    factory: SessionFactory,
    evaluation_id: uuid.UUID,
    worker_id: str,
    run_llm: RunLLM = run_structured,
    max_workers: int = MAX_WORKERS,
) -> None:
    run_id = _new_run_id()
    token = current_run_id.set(run_id)
    error_code: str | None = None
    error: str | None = None
    try:
        with factory() as s:
            ev = _owned(s, Evaluation, evaluation_id, worker_id)
            if ev is None:
                return
            if ev.status not in ("queued", "running"):
                ev.locked_by = ev.locked_until = None
                s.commit()
                return
            panel = s.get(PersonaPanel, ev.panel_id)
            sv = s.get(SiteVersion, ev.site_version_id)
            business = s.get(Business, ev.business_id)
            ev.status = "running"
            ev.run_id = run_id
            ev.attempts += 1
            prompt_v = agent.prompt_version(agent.EVAL_TASK)
            try:
                model = agent.assert_haiku()
                if ev.prompt_version not in (None, prompt_v) or ev.rubric_version not in (
                    None, RUBRIC_VERSION,
                ):  # fmt: skip
                    raise ReleaseUnavailable(
                        "The evaluation prompt or rubric changed since this evaluation started; "
                        "its runs would not be comparable."
                    )
                html = release_html(s, business, sv)
            except (ArenaError, ConfigError) as exc:
                error_code, error = _error_code(exc), _public_error(exc)
                s.commit()
                _finalize(factory, evaluation_id, worker_id, error_code, error)
                return
            ev.evaluator_model = ev.evaluator_model or model
            ev.prompt_version = prompt_v
            ev.rubric_version = RUBRIC_VERSION
            s.commit()
            personas = list(panel.personas_json or [])
            snapshot = {"id": ev.id, "panel_id": ev.panel_id, "site_version_id": ev.site_version_id}
            business_id = ev.business_id
        page = page_text(extract_visible_text(html))

        for _round in range(1 + EXTRA_ROUNDS):
            with factory() as s:
                todo = missing_combinations(s, evaluation_id, personas)
            if not todo:
                break
            fatal = False
            with ThreadPoolExecutor(max_workers=max(1, min(max_workers, MAX_WORKERS))) as pool:
                futures = {
                    pool.submit(
                        agent.evaluate_batch, page, batch,
                        business_id=business_id, run_id=run_id, run_llm=run_llm,
                    ): key
                    for key, batch in sorted(todo.items())
                }  # fmt: skip
                for fut in as_completed(futures):
                    b, r = futures[fut]
                    try:
                        results, model_used = fut.result()
                        persist_batch(factory, snapshot, b, r, results, model_used, run_id,
                                      worker_id)  # fmt: skip
                    except Exception as exc:  # one failed call never erases the others
                        error_code, error = _error_code(exc), _public_error(exc)
                        logger.warning("evaluation %s batch %s/%s failed: %s",
                                       evaluation_id, b, r, error)  # fmt: skip
                        fatal = fatal or error_code in _FATAL
            if fatal:
                break
        _finalize(factory, evaluation_id, worker_id, error_code, error)
    finally:
        current_run_id.reset(token)


def _finalize(
    factory: SessionFactory,
    evaluation_id: uuid.UUID,
    worker_id: str,
    error_code: str | None,
    error: str | None,
) -> None:
    with factory() as s:
        ev = _owned(s, Evaluation, evaluation_id, worker_id)
        if ev is None:
            return
        panel = s.get(PersonaPanel, ev.panel_id)
        runs = list(s.scalars(select(PersonaRun).where(PersonaRun.evaluation_id == ev.id)))
        personas = list(panel.personas_json or [])
        agg = aggregate(runs, personas, REPEATS)
        complete = len(runs) >= ev.expected_runs and agg["complete"]
        ev.aggregate_json = agg
        if complete:
            d, se = demand_score([p["convert_rate"] for p in agg["personas"]])
            ev.d_score = Decimal(str(display_score(d)))
            ev.d_se = Decimal(str(display_score(se)))
            ev.status, ev.error_code, ev.error = "complete", None, None
        else:
            ev.d_score = ev.d_se = None
            ev.status = "incomplete"
            ev.error_code = error_code or "evaluation_incomplete"
            ev.error = error or f"{len(runs)} of {ev.expected_runs} simulated evaluations completed"
        ev.locked_by = ev.locked_until = None
        sv = s.get(SiteVersion, ev.site_version_id)
        _audit(s, ev.business_id, worker_id, f"arena.evaluation_{ev.status}",
               f"Simulated evaluation of site v{sv.version}: {len(runs)}/{ev.expected_runs} runs"
               + (f", D={ev.d_score} (SE {ev.d_se})" if complete else ""),
               evaluation_id=str(ev.id), run_id=ev.run_id, error_code=ev.error_code)  # fmt: skip
        s.commit()


# --- deterministic aggregation (FR-AR-7) ----------------------------------------------------


def _run_view(r: Any) -> dict[str, Any]:
    get = r.get if isinstance(r, dict) else lambda k: getattr(r, k)
    return {
        "persona_id": get("persona_id"),
        "repeat": get("run_idx"),
        "decision": get("decision"),
        "objection_type": get("objection_type") or "other",
        "top_objection": (get("objection") or "").strip(),
        "section": get("section") or "none",
        "missing_info": list(get("missing_info") or []),
        "reason": get("reasoning") or "",
    }


def aggregate(runs: list[Any], personas: list[dict[str, Any]], repeats: int) -> dict[str, Any]:
    """Pure, deterministic summary of raw runs. One persistent issue is one group with a count
    (objection_type x section), never one task per persona. Labelled simulated."""
    views = sorted((_run_view(r) for r in runs), key=lambda v: (v["persona_id"], v["repeat"]))
    by_persona: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for v in views:
        by_persona[v["persona_id"]].append(v)

    persona_rows = []
    complete = bool(personas)
    for p in personas:
        pruns = by_persona.get(p["id"], [])
        if len(pruns) < repeats:
            complete = False
        converts = sum(v["decision"] == "convert" for v in pruns)
        persona_rows.append(
            {
                "persona_id": p["id"],
                "name": p.get("name"),
                "segment": p.get("segment"),
                "fit": p.get("fit"),
                "runs": [
                    {
                        k: v[k]
                        for k in (
                            "repeat",
                            "decision",
                            "reason",
                            "section",
                            "objection_type",
                            "top_objection",
                        )
                    }  # fmt: skip
                    for v in pruns
                ],
                "completed": len(pruns),
                "convert_rate": (converts / len(pruns)) if pruns else 0.0,
            }
        )

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for v in views:
        if v["decision"] != "bounce" and not v["top_objection"]:
            continue
        key = (v["objection_type"], v["section"])
        g = groups.setdefault(
            key, {"count": 0, "bounces": 0, "personas": set(), "texts": Counter()}
        )
        g["count"] += 1
        g["bounces"] += v["decision"] == "bounce"
        g["personas"].add(v["persona_id"])
        if v["top_objection"]:
            g["texts"][v["top_objection"]] += 1
    objections = [
        {
            "objection_type": t,
            "section": sec,
            "count": g["count"],
            "bounces": g["bounces"],
            "personas": len(g["personas"]),
            "example": min(g["texts"].items(), key=lambda kv: (-kv[1], kv[0]))[0]
            if g["texts"]
            else "",
        }
        for (t, sec), g in groups.items()
    ]
    objections.sort(key=lambda o: (-o["bounces"], -o["count"], o["objection_type"], o["section"]))

    friction = Counter(v["section"] for v in views if v["decision"] == "bounce")
    positive = Counter(
        v["section"] for v in views if v["decision"] == "convert" and v["section"] != "none"
    )
    missing: dict[str, dict[str, Any]] = {}
    for v in views:
        for item in v["missing_info"]:
            key = " ".join(item.lower().split())
            entry = missing.setdefault(key, {"item": item.strip(), "count": 0})
            entry["count"] += 1
    missing_ranked = sorted(missing.values(), key=lambda m: (-m["count"], m["item"].lower()))[:10]

    converted = sum(v["decision"] == "convert" for v in views)
    return {
        "simulated": True,
        "label": SIMULATED_LABEL,
        "complete": complete and len(views) == len(personas) * repeats,
        "total_runs": len(views),
        "converted": converted,
        "bounced": len(views) - converted,
        "personas": persona_rows,
        "objections": objections,
        "friction_by_section": dict(sorted(friction.items(), key=lambda kv: (-kv[1], kv[0]))),
        "positive_signals": dict(sorted(positive.items(), key=lambda kv: (-kv[1], kv[0]))),
        "missing_info": missing_ranked,
    }


# --- worker entry ---------------------------------------------------------------------------


def work_once(factory: SessionFactory, worker_id: str, run_llm: RunLLM = run_structured) -> bool:
    """Claim and run one arena job (panel first). Returns False if nothing was claimable."""
    with factory() as s:
        panel_id = claim_panel(s, worker_id)
    if panel_id is not None:
        run_panel_job(factory, panel_id, worker_id, run_llm)
        return True
    with factory() as s:
        evaluation_id = claim_evaluation(s, worker_id)
    if evaluation_id is not None:
        run_evaluation_job(factory, evaluation_id, worker_id, run_llm)
        return True
    return False
