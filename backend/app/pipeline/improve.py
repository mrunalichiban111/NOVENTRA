"""Improvement studio (M6: FR-IM-1..3, FR-IM-5, FR-IM-6, FR-GT-1, FR-GT-2).

Flow (the business stays LIVE; progress lives in improvement_attempts.status):
  start -> awaiting_answers (opportunities + founder questions, deterministic from M5 output)
  answers (source=user) -> proposal (one Sonnet call; Python whitelists, classifies, validates)
  approve items -> approved_for_draft -> [worker] draft_built: immutable v(N+1) rendered and
  published, NOT activated; same-panel Haiku evaluation queued -> draft_evaluated
  promote (founder click) -> [worker] gate re-checked, then M4's own `_deploy` activates and
  verifies (restoring the previous release on failure) -> promoted | not_promoted.
Gate: both evaluations complete and comparable, D_new >= D_original + 3 (fixed), explicit
founder approval (plus an approved Approval row for APPROVAL_REQUIRED items), and the source
version/content hash re-checked under the business row lock right before activation.
Classification is decided here from the paths touched, never by the model.
Manual rollback re-deploys an earlier verified version through the same M4 `_deploy` path.
"""

import copy
import logging
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.agents import improvement as rewrite_agent
from app.agents.premortem import RunLLM
from app.deploy import service
from app.deploy.providers import DeployProvider
from app.deploy.render import render_site
from app.deploy.site_model import TEMPLATE_VERSION, content_hash
from app.finance.scoring import ComparableKey, comparable
from app.llm.schemas import WebsiteContent
from app.llm.structured import run_structured
from app.models import (
    ATTEMPT_TERMINAL,
    Approval,
    Business,
    ImprovementAttempt,
    PersonaEvaluation,
    SiteVersion,
)
from app.models import BusinessStatus as S
from app.models import SiteVersionStatus as V
from app.pipeline import arena, status
from app.pipeline.status import Actor

logger = logging.getLogger(__name__)
SessionFactory = sessionmaker[Session]

PROMOTION_THRESHOLD = Decimal("3")  # SRS 6: keep v(N+1) only if D rises by >= 3 points
MAX_QUESTIONS = 5
MAX_SECTIONS = 3
ANSWER_MAX = 300

SAFE = "SAFE_AUTOMATIC"
FOUNDER = "FOUNDER_INPUT_REQUIRED"
APPROVAL = "APPROVAL_REQUIRED"
_RANK = {SAFE: 0, FOUNDER: 1, APPROVAL: 2}


class ImproveError(Exception):
    code = "improve_error"
    http = 409


class EvaluationIncomplete(ImproveError):
    code = "evaluation_incomplete"


class ProposalNotReady(ImproveError):
    code = "proposal_not_ready"


class ProposalStale(ImproveError):
    code = "proposal_stale"


class ProposalAlreadyApplied(ImproveError):
    code = "proposal_already_applied"


class ApprovalRequired(ImproveError):
    code = "approval_required"


class DraftNotImproved(ImproveError):
    code = "draft_not_improved"


class EvaluationInProgress(ImproveError):
    code = "evaluation_in_progress"


class NotFound(ImproveError):
    code = "not_found"
    http = 404


class InvalidInput(ImproveError):
    code = "invalid_input"
    http = 422


# --- classification (Python only) ------------------------------------------------------------


def classify_path(path: str, new_text: str = "") -> str:
    """APPROVAL_REQUIRED > FOUNDER_INPUT_REQUIRED > SAFE_AUTOMATIC; the highest rule wins."""
    level = SAFE
    if path.startswith("proof.founder_story") or re.search(r"\d", new_text):
        level = FOUNDER  # new facts or numbers: must trace to the founder
    if path.startswith("pricing.") or path == "proof.guarantees":
        level = APPROVAL  # price/plan or commitment changes
    return level


def classify_opportunity(objection_type: str, section: str) -> str:
    level = SAFE
    if objection_type in ("missing_info", "trust") or section == "proof":
        level = FOUNDER
    if objection_type == "price" or section == "pricing":
        level = APPROVAL
    return level


def highest(levels: list[str]) -> str:
    return max(levels, key=lambda lv: _RANK[lv], default=SAFE)


# --- editable fields (whitelist with WebsiteContent limits) ----------------------------------


def editable_fields(c: WebsiteContent) -> list[tuple[str, str, int]]:
    out = [
        ("hero.headline", c.hero.headline, 70),
        ("hero.subhead", c.hero.subhead, 160),
        ("hero.button_label", c.hero.button_label, 24),
    ]
    out += [(f"problem.{i}", t, 100) for i, t in enumerate(c.problem)]
    for i, o in enumerate(c.offer):
        out += [(f"offer.{i}.title", o.title, 40), (f"offer.{i}.body", o.body, 140)]
    for i, s in enumerate(c.how_it_works):
        out += [(f"how_it_works.{i}.title", s.title, 40), (f"how_it_works.{i}.body", s.body, 120)]
    out += [
        ("proof.founder_story", c.proof.founder_story, 300),
        ("proof.process", c.proof.process, 300),
        ("proof.guarantees", c.proof.guarantees, 300),
        ("pricing.0.name", c.pricing[0].name, 40),
    ]
    out += [(f"pricing.0.bullets.{i}", b, 80) for i, b in enumerate(c.pricing[0].bullets)]
    for i, f in enumerate(c.faq):
        out += [(f"faq.{i}.question", f.question, 120), (f"faq.{i}.answer", f.answer, 200)]
    out += [("cta.headline", c.cta.headline, 70), ("cta.button_label", c.cta.button_label, 24)]
    return out


def _set_path(data: dict[str, Any], path: str, value: str) -> None:
    parts = path.split(".")
    node: Any = data
    for p in parts[:-1]:
        node = node[int(p)] if p.isdigit() else node[p]
    last = parts[-1]
    if last.isdigit():
        node[int(last)] = value
    else:
        node[last] = value


_NUM = re.compile(r"\d+(?:[.,]\d+)*")


def _numbers(text: str) -> set[str]:
    return {m.group(0).replace(",", "") for m in _NUM.finditer(text)}


def apply_items(content: dict[str, Any], items: list[dict[str, Any]]) -> WebsiteContent:
    """Apply validated replacements to a COPY and re-validate with WebsiteContent."""
    data = copy.deepcopy(content)
    for it in items:
        _set_path(data, it["path"], it["new"])
    return WebsiteContent.model_validate(data)


def validate_proposal(
    content: dict[str, Any], raw_items: list[Any], facts_text: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(accepted items, rejected items with reasons). Only whitelisted paths, at most 3
    sections, numbers traced to existing text or founder facts, each item valid on its own."""
    current = WebsiteContent.model_validate(content)
    fields = {p: (t, lim) for p, t, lim in editable_fields(current)}
    known_numbers = _numbers(" ".join(t for t, _ in fields.values()) + " " + facts_text)
    accepted: list[dict[str, Any]] = []
    rejected: list[dict[str, Any]] = []
    sections: list[str] = []
    seen: set[str] = set()
    for raw in raw_items:
        path, new = raw.path.strip(), raw.new_text.strip()
        reason = None
        if path not in fields:
            reason = "unknown_or_locked_path"
        elif path in seen:
            reason = "duplicate_path"
        elif len(new) > fields[path][1]:
            reason = "too_long"
        elif new == fields[path][0]:
            reason = "unchanged"
        elif not _numbers(new) <= known_numbers:
            reason = "unsupported_number"
        section = path.split(".")[0]
        if reason is None and section not in sections and len(sections) >= MAX_SECTIONS:
            reason = "too_many_sections"
        if reason is None:
            try:
                apply_items(content, [{"path": path, "new": new}])
            except Exception as exc:  # WebsiteContent limits and content rules (HTML, links...)
                reason = f"invalid: {str(exc).splitlines()[0][:120]}"
        if reason is not None:
            rejected.append({"path": path, "new": new[:300], "reason": reason})
            continue
        seen.add(path)
        if section not in sections:
            sections.append(section)
        accepted.append(
            {
                "id": f"i{len(accepted) + 1}",
                "path": path,
                "section": section,
                "old": fields[path][0],
                "new": new,
                "rationale": raw.rationale,
                "opportunity_id": raw.opportunity_id,
                "classification": classify_path(path, new),
                "decision": "pending",
            }
        )
    if accepted:
        apply_items(content, accepted)  # all accepted items together must still be valid
    return accepted, rejected


# --- questions (deterministic templates) -----------------------------------------------------


def build_questions(opps: list[dict[str, Any]], missing: list[dict[str, Any]]) -> list[dict]:
    qs: list[dict[str, Any]] = []

    def add(key: str, question: str, opp: str | None) -> None:
        if len(qs) < MAX_QUESTIONS and all(q["field_key"] != key for q in qs):
            qs.append(
                {
                    "id": f"q{len(qs) + 1}",
                    "field_key": key,
                    "question": question,
                    "answer_type": "text",
                    "required": True,
                    "options": None,
                    "opportunity_id": opp,
                }
            )

    for o in opps:
        if o["objection_type"] == "price" or o["section"] == "pricing":
            add("pricing", "What price (INR) and plan details should the page show? "
                "Write 'not decided' if you are not sure yet.", o["id"])  # fmt: skip
        elif o["objection_type"] == "trust" or o["section"] == "proof":
            add("trust", "Who is behind the business, and what quality or safety steps can "
                "visitors rely on?", o["id"])  # fmt: skip
    for m in missing:
        item = m["item"]
        slug = re.sub(r"[^a-z0-9]+", "_", item.lower()).strip("_")[:30] or "info"
        add(f"missing_{slug}", f"Simulated visitors looked for: “{item}”. What is "
            "true for your business?", None)  # fmt: skip
    return qs


# --- reads -------------------------------------------------------------------------------------


def open_attempt(session: Session, business_id: uuid.UUID) -> ImprovementAttempt | None:
    return session.scalar(
        select(ImprovementAttempt)
        .where(
            ImprovementAttempt.business_id == business_id,
            ImprovementAttempt.status.not_in(ATTEMPT_TERMINAL),
        )
        .execution_options(populate_existing=True)
    )


def latest_attempt(session: Session, business_id: uuid.UUID) -> ImprovementAttempt | None:
    return session.scalar(
        select(ImprovementAttempt)
        .where(ImprovementAttempt.business_id == business_id)
        .order_by(ImprovementAttempt.created_at.desc())
        .limit(1)
        .execution_options(populate_existing=True)
    )


def _key(ev: PersonaEvaluation) -> ComparableKey:
    return ComparableKey(str(ev.panel_id), ev.evaluator_model, ev.prompt_version, ev.rubric_version)


def gate(original: PersonaEvaluation | None, draft: PersonaEvaluation | None) -> str | None:
    """None if the D rule passes, else the stable error code."""
    if original is None or draft is None:
        return "evaluation_incomplete"
    ok = comparable(
        _key(original), original.status == "complete", _key(draft), draft.status == "complete"
    )
    if not ok or original.d_score is None or draft.d_score is None:
        return "evaluation_incomplete"
    if draft.d_score < original.d_score + PROMOTION_THRESHOLD:
        return "draft_not_improved"
    return None


def _audit(session: Session, business: Business, action: str, reason: str, actor: Actor,
           **details: Any) -> None:  # fmt: skip
    status.record_event(session, business, actor=actor, action=action, reason=reason,
                        details=details)  # fmt: skip


def _lock_attempt(session: Session, business_id: uuid.UUID) -> tuple[Business, ImprovementAttempt]:
    business = status.lock_business(session, business_id)
    attempt = session.scalar(
        select(ImprovementAttempt)
        .where(ImprovementAttempt.business_id == business_id)
        .order_by(ImprovementAttempt.created_at.desc())
        .limit(1)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if attempt is None:
        raise ProposalNotReady("Start an improvement first.")
    return business, attempt


def _is_stale(session: Session, attempt: ImprovementAttempt) -> bool:
    live = service.deployed_version(session, attempt.business_id)
    return (
        live is None
        or live.version != attempt.source_version
        or live.content_hash != attempt.source_content_hash
    )


# --- API-side actions (each commits) ----------------------------------------------------------


def start(session: Session, business_id: uuid.UUID) -> tuple[ImprovementAttempt, bool]:
    business = status.lock_business(session, business_id)
    existing = open_attempt(session, business_id)
    if existing is not None:
        session.commit()
        return existing, False
    live = service.deployed_version(session, business_id)
    found = arena.get_evaluation(session, business_id, live.version if live else None)
    if live is None or found is None or found[0].status != "complete":
        session.rollback()
        raise EvaluationIncomplete("Run a complete simulated evaluation of the live site first.")
    ev = found[0]
    agg = ev.aggregate_json or {}
    opps = []
    for o in (agg.get("objections") or [])[:5]:
        opps.append(
            {
                "id": f"{o['objection_type']}:{o['section']}",
                "objection_type": o["objection_type"],
                "section": o["section"],
                "evidence_count": o["count"],
                "bounces": o["bounces"],
                "personas": o["personas"],
                "example": o.get("example", ""),
                "classification": classify_opportunity(o["objection_type"], o["section"]),
                "status": "open",
            }
        )
    attempt = ImprovementAttempt(
        business_id=business_id,
        from_version=live.version,
        status="awaiting_answers",
        clusters_json=opps,
        questions_json=build_questions(opps, agg.get("missing_info") or []),
        answers_json={},
        source_version=live.version,
        source_content_hash=live.content_hash,
        original_evaluation_id=ev.id,
    )
    session.add(attempt)
    session.flush()
    _audit(session, business, "improve.started", f"Improvement started from site v{live.version}",
           Actor.user(), attempt_id=str(attempt.id), opportunities=len(opps))  # fmt: skip
    session.commit()
    return attempt, True


def answer(
    session: Session, business_id: uuid.UUID, qid: str, value: str | None, not_decided: bool
) -> ImprovementAttempt:
    business, attempt = _lock_attempt(session, business_id)
    if attempt.status != "awaiting_answers":
        session.rollback()
        raise ProposalAlreadyApplied("Answers are locked once a proposal exists.")
    if qid not in {q["id"] for q in attempt.questions_json}:
        session.rollback()
        raise NotFound(f"Unknown question {qid}")
    text = (value or "").strip()
    if not not_decided and not text:
        session.rollback()
        raise InvalidInput("Give an answer or mark it as not decided yet.")
    if len(text) > ANSWER_MAX:
        session.rollback()
        raise InvalidInput(f"Answers are limited to {ANSWER_MAX} characters.")
    answers = dict(attempt.answers_json or {})
    answers[qid] = {
        "value": None if not_decided else text,
        "status": "not_decided" if not_decided else "answered",
        "source": "user",
        "at": datetime.now(UTC).isoformat(),
    }
    attempt.answers_json = answers
    _audit(session, business, "improve.answered", f"Founder answered {qid}", Actor.user(),
           attempt_id=str(attempt.id), question=qid, status=answers[qid]["status"])  # fmt: skip
    session.commit()
    return attempt


def generate_proposal(
    session: Session, business_id: uuid.UUID, run_llm: RunLLM = run_structured
) -> ImprovementAttempt:
    business, attempt = _lock_attempt(session, business_id)
    if attempt.status != "awaiting_answers":
        session.commit()
        if attempt.status in ATTEMPT_TERMINAL:
            raise ProposalNotReady("Start a new improvement first.")
        return attempt  # idempotent: proposal already exists
    missing = [
        q["id"] for q in attempt.questions_json if q["id"] not in (attempt.answers_json or {})
    ]
    if missing:
        session.rollback()
        raise ProposalNotReady(f"Answer (or mark not decided) every question first: {missing}")
    if _is_stale(session, attempt):
        attempt.status, attempt.reason = "stale", "The live site changed since this started."
        session.commit()
        raise ProposalStale("The live site changed; start a new improvement.")
    live = service.deployed_version(session, business_id)
    content = WebsiteContent.model_validate(live.content_json)
    qtext = {q["id"]: q["question"] for q in attempt.questions_json}
    answers = [
        (qtext[qid], a["value"] if a["status"] == "answered" else "not decided")
        for qid, a in (attempt.answers_json or {}).items()
    ]
    prompt = rewrite_agent.rewrite_prompt(editable_fields(content), attempt.clusters_json, answers)
    facts = " ".join(a for _, a in answers) + (
        f" {business.price_guess}" if business.price_guess is not None else ""
    )
    attempt_id = attempt.id
    session.commit()  # release the lock during the model call
    run_id = f"rewrite-{uuid.uuid4().hex[:12]}"
    proposal, model = rewrite_agent.generate_rewrite(
        prompt, business_id=business_id, run_id=run_id, run_llm=run_llm
    )
    accepted, rejected = validate_proposal(live.content_json, proposal.items, facts)
    business, attempt = _lock_attempt(session, business_id)
    if attempt.id != attempt_id or attempt.status != "awaiting_answers":
        session.commit()
        return attempt
    if not accepted:
        attempt.proposal_json = {
            "items": [],
            "rejected": rejected,
            "model": model,
            "run_id": run_id,
        }
        session.commit()
        raise ProposalNotReady("No proposed change passed validation; try again.")
    attempt.proposal_json = {
        "items": accepted,
        "rejected": rejected,
        "model": model,
        "run_id": run_id,
        "classification": highest([i["classification"] for i in accepted]),
    }
    attempt.changes_json = accepted
    attempt.status = "proposed"
    _audit(session, business, "improve.proposed",
           f"{len(accepted)} change(s) proposed, {len(rejected)} rejected by validation",
           Actor.user(), attempt_id=str(attempt.id), run_id=run_id)  # fmt: skip
    session.commit()
    return attempt


def approve(
    session: Session, business_id: uuid.UUID, item_ids: list[str], approve_consequential: bool
) -> ImprovementAttempt:
    business, attempt = _lock_attempt(session, business_id)
    if attempt.status != "proposed":
        session.rollback()
        raise ProposalAlreadyApplied(f"The proposal is {attempt.status}.")
    if _is_stale(session, attempt):
        attempt.status, attempt.reason = "stale", "The live site changed since the proposal."
        session.commit()
        raise ProposalStale("The live site changed; start a new improvement.")
    items = attempt.proposal_json.get("items", [])
    chosen = [i for i in items if i["id"] in set(item_ids)]
    if not chosen:
        session.rollback()
        raise InvalidInput("Approve at least one proposed change.")
    consequential = [i for i in chosen if i["classification"] == APPROVAL]
    if consequential and not approve_consequential:
        session.rollback()
        raise ApprovalRequired("Price, plan or guarantee changes need your explicit approval.")
    if consequential:
        appr = Approval(
            business_id=business_id,
            action_type="improve.consequential_change",
            payload_json={"attempt_id": str(attempt.id), "items": [i["id"] for i in consequential]},
            status="approved",
            decided_at=datetime.now(UTC),
        )
        session.add(appr)
        session.flush()
        attempt.approval_id = appr.id
    new_items = []
    for i in items:
        i = dict(i)
        i["decision"] = "approved" if i["id"] in set(item_ids) else "rejected"
        new_items.append(i)
    attempt.proposal_json = {**attempt.proposal_json, "items": new_items}
    attempt.status = "approved_for_draft"
    _audit(session, business, "improve.approved_for_draft",
           f"Founder approved {len(chosen)} change(s) for a draft", Actor.user(),
           attempt_id=str(attempt.id))  # fmt: skip
    session.commit()
    return attempt


def _discard_draft(session: Session, attempt: ImprovementAttempt) -> None:
    if attempt.draft_site_version_id is None:
        return
    sv = session.get(SiteVersion, attempt.draft_site_version_id, with_for_update=True)
    if sv is not None and sv.status in (V.BUILT, V.FAILED, V.DRAFT):
        sv.status = V.DISCARDED


def reject(session: Session, business_id: uuid.UUID) -> ImprovementAttempt:
    business, attempt = _lock_attempt(session, business_id)
    if attempt.status not in ("awaiting_answers", "proposed"):
        session.rollback()
        raise ProposalAlreadyApplied(f"The proposal is {attempt.status}.")
    attempt.status, attempt.reason = "rejected", "Rejected by the founder"
    _audit(session, business, "improve.rejected", "Founder rejected the proposal", Actor.user(),
           attempt_id=str(attempt.id))  # fmt: skip
    session.commit()
    return attempt


def discard(session: Session, business_id: uuid.UUID) -> ImprovementAttempt:
    business, attempt = _lock_attempt(session, business_id)
    if attempt.status not in ("draft_built", "draft_evaluated", "approved_for_draft"):
        session.rollback()
        raise ProposalAlreadyApplied(f"The proposal is {attempt.status}.")
    pj = attempt.proposal_json or {}
    if pj.get("promote_requested") or pj.get("promoting"):
        session.rollback()
        raise ProposalAlreadyApplied("Promotion is already in progress.")
    _discard_draft(session, attempt)
    attempt.status, attempt.outcome = "discarded", "discarded"
    attempt.reason = "Discarded by the founder; the original stays live"
    _audit(session, business, "improve.discarded", attempt.reason, Actor.user(),
           attempt_id=str(attempt.id))  # fmt: skip
    session.commit()
    return attempt


def _not_promoted(session: Session, business: Business, attempt: ImprovementAttempt,
                  code: str, actor: Actor) -> None:  # fmt: skip
    _discard_draft(session, attempt)
    attempt.status, attempt.outcome, attempt.reason = "not_promoted", "not_promoted", code
    _audit(session, business, "improve.not_promoted",
           f"Draft not promoted ({code}); the original release stays live", actor,
           attempt_id=str(attempt.id), code=code)  # fmt: skip


def request_promotion(session: Session, business_id: uuid.UUID) -> ImprovementAttempt:
    """Founder click. Gate failures mark the attempt not_promoted (draft kept for audit) and
    raise the stable code; a passing gate queues activation for the worker."""
    business, attempt = _lock_attempt(session, business_id)
    if attempt.status in ("promoted", "not_promoted"):
        session.rollback()
        raise ProposalAlreadyApplied(f"The proposal is already {attempt.status}.")
    if attempt.status != "draft_evaluated":
        session.rollback()
        raise EvaluationInProgress("The draft is still being built or evaluated.")
    if (attempt.proposal_json or {}).get("promote_requested") or (attempt.proposal_json or {}).get(
        "promoting"
    ):
        session.commit()
        return attempt
    original = session.get(PersonaEvaluation, attempt.original_evaluation_id)
    draft = session.get(PersonaEvaluation, attempt.draft_evaluation_id)
    code = gate(original, draft)
    if code is None and _is_stale(session, attempt):
        code = "proposal_stale"
    if (
        code is None
        and attempt.approval_id is None
        and any(
            i["decision"] == "approved" and i["classification"] == APPROVAL
            for i in attempt.proposal_json.get("items", [])
        )
    ):
        code = "approval_not_granted"
    if code is not None:
        _not_promoted(session, business, attempt, code, Actor.user())
        session.commit()
        err = {
            "evaluation_incomplete": EvaluationIncomplete,
            "draft_not_improved": DraftNotImproved,
            "proposal_stale": ProposalStale,
        }.get(code, ApprovalRequired)
        raise err(f"Not promoted: {code}. The original release stays live.")
    appr = Approval(
        business_id=business_id,
        action_type="improve.promote",
        payload_json={"attempt_id": str(attempt.id), "draft_version": attempt.to_version},
        status="approved",
        decided_at=datetime.now(UTC),
    )
    session.add(appr)
    attempt.proposal_json = {**attempt.proposal_json, "promote_requested": True}
    _audit(session, business, "improve.promote_requested",
           f"Founder approved promoting draft v{attempt.to_version}", Actor.user(),
           attempt_id=str(attempt.id))  # fmt: skip
    session.commit()
    return attempt


def request_rollback(session: Session, business_id: uuid.UUID, to_version: int) -> Approval:
    """Queue a manual rollback (worker re-deploys an earlier verified version via M4 `_deploy`)."""
    business = status.lock_business(session, business_id)
    sv = session.scalar(
        select(SiteVersion).where(
            SiteVersion.business_id == business_id, SiteVersion.version == to_version
        )
    )
    if business.status != S.LIVE or sv is None or sv.status not in (V.SUPERSEDED, V.ROLLED_BACK):
        session.rollback()
        raise InvalidInput(f"Site v{to_version} is not an earlier live version.")
    pending = session.scalar(
        select(Approval).where(
            Approval.business_id == business_id,
            Approval.action_type == "site.rollback",
            Approval.payload_json["executed"].as_boolean().is_(False),
        )
    )
    if pending is not None:
        session.commit()
        return pending
    appr = Approval(
        business_id=business_id,
        action_type="site.rollback",
        payload_json={"to_version": to_version, "executed": False},
        status="approved",
        decided_at=datetime.now(UTC),
    )
    session.add(appr)
    _audit(session, business, "site.rollback_requested", f"Rollback to v{to_version} requested",
           Actor.user(), to_version=to_version)  # fmt: skip
    session.commit()
    return appr


# --- worker jobs --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Deps:
    provider_factory: Callable[[], DeployProvider] | None = None

    def context(self) -> service.DeployContext:
        return service.make_context(run_structured, provider_factory=self.provider_factory)


def _claim_attempt(session: Session, statuses: list[str]) -> ImprovementAttempt | None:
    return session.scalar(
        select(ImprovementAttempt)
        .where(ImprovementAttempt.status.in_(statuses))
        .order_by(ImprovementAttempt.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )


def build_draft(session: Session, attempt: ImprovementAttempt, deps: Deps, worker: Actor) -> None:
    """approved_for_draft -> draft_built: immutable v(N+1), published, never activated."""
    business = status.lock_business(session, attempt.business_id)
    if _is_stale(session, attempt):
        attempt.status, attempt.reason = "stale", "The live site changed before the draft."
        _audit(session, business, "improve.stale", attempt.reason, worker,
               attempt_id=str(attempt.id))  # fmt: skip
        return
    live = service.deployed_version(session, attempt.business_id)
    approved = [i for i in attempt.proposal_json.get("items", []) if i["decision"] == "approved"]
    new_content = apply_items(live.content_json, approved)
    claim = service.load_claim(session, business)
    ctx = deps.context()
    site = render_site(
        new_content, business_name=claim.name, host=claim.host,
        price_guess=business.price_guess, links=ctx.links,
    )  # fmt: skip
    next_version = (
        session.scalar(
            select(SiteVersion.version)
            .where(SiteVersion.business_id == business.id)
            .order_by(SiteVersion.version.desc())
            .limit(1)
        )
        or 0
    ) + 1
    ctx.provider.publish(claim.host, next_version, site)
    sv = SiteVersion(
        business_id=business.id,
        version=next_version,
        content_json=new_content.model_dump(mode="json"),
        content_hash=content_hash(new_content),
        status=V.BUILT,
        parent_version=live.version,
        host=claim.host,
        template_version=TEMPLATE_VERSION,
        artifact_hash=site.artifact_hash,
        manifest_json=site.manifest,
        path=f"{claim.host}/releases/v{next_version}",
    )
    session.add(sv)
    session.flush()
    panel = arena.get_panel(session, business.id)
    ev = PersonaEvaluation(
        business_id=business.id,
        panel_id=panel.id,
        site_version_id=sv.id,
        status="queued",
        expected_runs=arena.EXPECTED_RUNS,
    )
    session.add(ev)
    session.flush()
    attempt.draft_site_version_id = sv.id
    attempt.to_version = next_version
    attempt.draft_evaluation_id = ev.id
    attempt.status = "draft_built"
    _audit(session, business, "improve.draft_built",
           f"Draft v{next_version} built (not activated); simulated evaluation queued", worker,
           attempt_id=str(attempt.id), draft_version=next_version)  # fmt: skip


def mark_evaluated(session: Session, attempt: ImprovementAttempt, worker: Actor) -> bool:
    ev = session.get(PersonaEvaluation, attempt.draft_evaluation_id)
    if ev is None or ev.status not in ("complete", "incomplete"):
        return False
    original = session.get(PersonaEvaluation, attempt.original_evaluation_id)
    if gate(original, ev) != "evaluation_incomplete":
        attempt.delta_demand = ev.d_score - original.d_score
    attempt.status = "draft_evaluated"
    business = session.get(Business, attempt.business_id)
    _audit(session, business, "improve.draft_evaluated",
           f"Draft v{attempt.to_version} evaluated: {ev.status}", worker,
           attempt_id=str(attempt.id), delta=str(attempt.delta_demand))  # fmt: skip
    return True


def promote(session: Session, attempt: ImprovementAttempt, deps: Deps, worker: Actor) -> None:
    """Re-check the full gate under the business lock, then activate via M4 `_deploy`."""
    business = status.lock_business(session, attempt.business_id)
    original = session.get(PersonaEvaluation, attempt.original_evaluation_id)
    draft_ev = session.get(PersonaEvaluation, attempt.draft_evaluation_id)
    code = gate(original, draft_ev)
    if code is None and _is_stale(session, attempt):
        code = "proposal_stale"
    if code is None and business.status != S.LIVE:
        code = "not_live"
    if code is not None:
        _not_promoted(session, business, attempt, code, worker)
        return
    sv = session.get(SiteVersion, attempt.draft_site_version_id)
    claim = service.load_claim(session, business)
    ctx = deps.context()
    # Mark in progress before releasing the locks so no other worker claims this promotion.
    attempt.proposal_json = {**attempt.proposal_json, "promote_requested": False, "promoting": True}
    session.commit()  # the deploy path takes its own checkpoints (row lock + LIVE check)
    try:
        service._ensure_published(business, claim, ctx, sv)
        service._deploy(session, business, claim, ctx, sv, expected=S.LIVE)
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("promotion of v%s failed: %s", sv.version, exc)
        business = status.lock_business(session, attempt.business_id)
        attempt = session.get(ImprovementAttempt, attempt.id, populate_existing=True)
        _not_promoted(session, business, attempt, "deploy_failed", worker)
        return
    business = status.lock_business(session, attempt.business_id)
    attempt = session.get(ImprovementAttempt, attempt.id, populate_existing=True)
    attempt.status, attempt.outcome = "promoted", "promoted"
    attempt.reason = f"D +{attempt.delta_demand} (>= {PROMOTION_THRESHOLD}); founder approved"
    _audit(session, business, "improve.promoted", f"Draft v{sv.version} promoted and verified",
           worker, attempt_id=str(attempt.id), version=sv.version)  # fmt: skip


def rollback(session: Session, appr: Approval, deps: Deps, worker: Actor) -> None:
    business = status.lock_business(session, appr.business_id)
    to_version = int(appr.payload_json["to_version"])
    sv = session.scalar(
        select(SiteVersion).where(
            SiteVersion.business_id == business.id, SiteVersion.version == to_version
        )
    )
    current = service.deployed_version(session, business.id)
    appr.payload_json = {**appr.payload_json, "executed": True}
    session.commit()
    try:
        claim = service.load_claim(session, business)
        ctx = deps.context()
        service._ensure_published(business, claim, ctx, sv)
        service._deploy(session, business, claim, ctx, sv, expected=S.LIVE)
        if current is not None:
            old = session.get(SiteVersion, current.id, populate_existing=True)
            if old.status == V.SUPERSEDED:
                old.status = V.ROLLED_BACK
        session.commit()
        outcome, reason = "site.rolled_back", f"Rolled back to v{to_version}"
    except Exception as exc:
        session.rollback()
        outcome, reason = "site.rollback_failed", f"Rollback to v{to_version} failed: {exc}"[:500]
    business = status.lock_business(session, appr.business_id)
    a = session.get(Approval, appr.id, populate_existing=True)
    a.payload_json = {**a.payload_json, "result": outcome}
    _audit(session, business, outcome, reason, worker, to_version=to_version)


def work_once(factory: SessionFactory, worker_id: str, deps: Deps | None = None) -> bool:
    deps = deps or Deps()
    worker = Actor.worker(worker_id)
    with factory() as s:
        a = _claim_attempt(s, ["approved_for_draft"])
        if a is not None:
            try:
                build_draft(s, a, deps, worker)
                s.commit()
            except Exception as exc:
                s.rollback()
                logger.exception("draft build failed")
                a = s.get(ImprovementAttempt, a.id, with_for_update=True)
                business = status.lock_business(s, a.business_id)
                a.status, a.outcome, a.reason = (
                    "not_promoted",
                    "not_promoted",
                    (f"draft_build_failed: {exc}"[:500]),
                )
                _audit(s, business, "improve.draft_failed", a.reason, worker,
                       attempt_id=str(a.id))  # fmt: skip
                s.commit()
            return True
    with factory() as s:
        for a in s.scalars(
            select(ImprovementAttempt)
            .where(ImprovementAttempt.status == "draft_built")
            .with_for_update(skip_locked=True)
        ):
            if mark_evaluated(s, a, worker):
                s.commit()
                return True
        s.rollback()
    with factory() as s:
        a = s.scalar(
            select(ImprovementAttempt)
            .where(
                ImprovementAttempt.status == "draft_evaluated",
                ImprovementAttempt.proposal_json["promote_requested"].as_boolean().is_(True),
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if a is not None:
            promote(s, a, deps, worker)
            s.commit()
            return True
    with factory() as s:
        appr = s.scalar(
            select(Approval)
            .where(
                Approval.action_type == "site.rollback",
                Approval.status == "approved",
                Approval.payload_json["executed"].as_boolean().is_(False),
            )
            .limit(1)
            .with_for_update(skip_locked=True)
        )
        if appr is not None:
            rollback(s, appr, deps, worker)
            s.commit()
            return True
    return False


SIMULATED_LABEL = arena.SIMULATED_LABEL
