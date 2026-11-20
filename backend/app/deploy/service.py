"""Site generation and deployment orchestration (M4B: FR-SITE-1..5, FR-GT-2, FR-PR-7).

`request_deploy` (API side, wired in Block 5) moves DOMAIN_PENDING -> DEPLOYING.
`run_deploy_stage` (worker side) does the DEPLOYING stage and returns LIVE only after the
deployment reached the required verification level. It resumes from the first missing step:

  1. content   open SiteVersion `generating` committed BEFORE the paid model call; the
               validated content is committed as `draft` immediately after it.
  2. build     render + validate + immutable publish of releases/vN; recorded as `built`.
  3. deploy    a `deployments` row per attempt (started -> activated -> verified | failed);
               atomic symlink activation; verification; on failure the previous verified
               release is re-activated and the version is marked `failed` (artifact kept).

Crash recovery (M4B R4): a `generating` row left by another run means a paid call may have
happened without its result being stored. It is marked `abandoned` (with the spend recorded
for that run) and the stage FAILS instead of silently paying again. Only an explicit, audited
`pipeline.retry` after that may start a new generation attempt for the same version.

Version allocation happens under the business row lock (checkpoint) and is backed by the
unique indexes from migration 0005. The domain claim (DomainCandidate.selected and
Business.domain) is read here and never modified, so every failure preserves it.
"""

import logging
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import config
from app.agents.premortem import RunLLM
from app.agents.site_content import generate_site_content
from app.deploy.health import acceptable_for_live
from app.deploy.hosts import HostError, validate_host
from app.deploy.providers import DeployProvider, DeploySettings, get_provider
from app.deploy.render import RenderedSite, render_site
from app.deploy.site_model import TEMPLATE_VERSION, SiteLinks, content_hash
from app.llm.schemas import WebsiteContent
from app.models import (
    SITE_OPEN_STATUSES,
    AuditLog,
    Business,
    BusinessStatus,
    Deployment,
    DeploymentStatus,
    DomainCandidate,
    LlmUsage,
    SiteVersion,
)
from app.models import SiteVersionStatus as V
from app.pipeline import status
from app.pipeline.checkpoint import checkpoint, model_call_lease_s
from app.pipeline.runner import StageResult, current_run_id, current_worker_id
from app.pipeline.status import Actor

S = BusinessStatus
logger = logging.getLogger(__name__)


class DeployError(Exception):
    """A deploy-stage failure. The runner records it as FAILED (retryable)."""


class DeployInputError(DeployError):
    """The business lacks a valid selected name / claimed subdomain."""


class GenerationAbandoned(DeployError):
    """A previous generation was interrupted; an explicit retry is required before paying again."""


class DeploymentFailed(DeployError):
    """Activation or verification failed; the previous verified release was restored if any."""


class NotEligible(DeployError):
    """Deploy requested for a business that is not waiting in DOMAIN_PENDING."""


@dataclass(frozen=True)
class Claim:
    host: str
    name: str
    slug: str


@dataclass(frozen=True)
class DeployContext:
    provider: DeployProvider
    links: SiteLinks
    require_public: bool
    run_llm: RunLLM
    verify_lease_s: float  # lease to hold while activation + verification run


# --- queries ------------------------------------------------------------------------------


def load_claim(session: Session, business: Business) -> Claim:
    """The selected name and claimed host. Raises DeployInputError; never modifies the claim."""
    cand = session.scalar(
        select(DomainCandidate).where(
            DomainCandidate.business_id == business.id, DomainCandidate.selected.is_(True)
        )
    )
    if cand is None or not cand.slug or not business.domain:
        raise DeployInputError("No business name is selected / no subdomain is claimed.")
    try:
        host = validate_host(business.domain)
    except HostError as exc:
        raise DeployInputError(f"Claimed host is not valid: {exc}") from None
    if host != f"{cand.slug}.{config.BASE_DOMAIN}":
        raise DeployInputError("Claimed host does not match the selected name.")
    return Claim(host=host, name=cand.name, slug=cand.slug)


def open_version(session: Session, business_id: uuid.UUID) -> SiteVersion | None:
    return session.scalar(
        select(SiteVersion)
        .where(SiteVersion.business_id == business_id, SiteVersion.status.in_(SITE_OPEN_STATUSES))
        .execution_options(populate_existing=True)
    )


def deployed_version(
    session: Session, business_id: uuid.UUID, *, exclude: uuid.UUID | None = None
) -> SiteVersion | None:
    q = select(SiteVersion).where(
        SiteVersion.business_id == business_id, SiteVersion.status == V.DEPLOYED
    )
    if exclude is not None:
        q = q.where(SiteVersion.id != exclude)
    return session.scalar(q.order_by(SiteVersion.version.desc()).limit(1))


def _retry_since(session: Session, business_id: uuid.UUID, since: Any) -> bool:
    """Was there an explicit retry (audited pipeline.retry) after `since`?"""
    return (
        session.scalar(
            select(func.count())
            .select_from(AuditLog)
            .where(
                AuditLog.business_id == business_id,
                AuditLog.action == "pipeline.retry",
                AuditLog.ts > since,
            )
        )
        > 0
    )


def _spend(session: Session, run_id: str | None) -> Decimal:
    if not run_id:
        return Decimal("0")
    total = session.scalar(
        select(func.coalesce(func.sum(LlmUsage.estimated_cost_usd), 0)).where(
            LlmUsage.run_id == run_id, LlmUsage.task == "website_content"
        )
    )
    return Decimal(total)


def _audit(
    session: Session,
    business: Business,
    action: str,
    reason: str,
    key: str,
    version: int,
    **details: Any,
) -> None:
    status.record_event(
        session,
        business,
        actor=_actor(),
        action=action,
        reason=reason,
        version_ref=f"site-v{version}",
        details=details,
        key=key,
    )


def _actor() -> Actor:
    worker = current_worker_id.get()
    return Actor.worker(worker) if worker else Actor("agent:deploy")


def _row(session: Session, version_id: uuid.UUID) -> SiteVersion:
    row = session.get(SiteVersion, version_id, with_for_update=True, populate_existing=True)
    if row is None:  # pragma: no cover - rows are never deleted by the app
        raise status.StaleState(f"site version {version_id} vanished")
    return row


# --- step 1: content ---------------------------------------------------------------------


def _start_generation(
    session: Session, business: Business, claim: Claim, run_id: str
) -> tuple[str, uuid.UUID, int]:
    """Allocate or reclaim the open version under the business lock.

    Returns (outcome, version_id, version): outcome is "generate" or "abandoned".
    """

    def apply(locked: Business) -> tuple[str, uuid.UUID, int]:
        locked.locked_until = func.now() + timedelta(seconds=model_call_lease_s("website_content"))
        row = open_version(session, locked.id)
        if row is None:
            last = session.scalar(
                select(func.max(SiteVersion.version)).where(SiteVersion.business_id == locked.id)
            )
            parent = deployed_version(session, locked.id)
            row = SiteVersion(
                business_id=locked.id,
                version=(last or 0) + 1,
                status=V.GENERATING,
                host=claim.host,
                template_version=TEMPLATE_VERSION,
                parent_version=parent.version if parent else None,
                generation_run_id=run_id,
                generation_attempt=1,
                generation_started_at=func.now(),
            )
            session.add(row)
            session.flush()
        elif row.status == V.GENERATING and row.generation_run_id != run_id:
            spent = _spend(session, row.generation_run_id)
            row.status = V.ABANDONED
            row.error = (
                f"Generation attempt {row.generation_attempt} was interrupted (worker crash or "
                f"lost lease); recorded spend for that run: ${spent}. Retry to generate again."
            )
            _audit(
                session,
                locked,
                "site.generation_abandoned",
                row.error,
                f"{locked.id}:site:v{row.version}:gen{row.generation_attempt}:abandoned",
                row.version,
                run_id=row.generation_run_id,
                spend_usd=str(spent),
            )
            return "abandoned", row.id, row.version
        elif row.status == V.ABANDONED:
            if not _retry_since(session, locked.id, row.updated_at):
                return "abandoned", row.id, row.version
            row.status = V.GENERATING
            row.generation_attempt += 1
            row.generation_run_id = run_id
            row.generation_started_at = func.now()
            row.error = None
        elif row.status != V.GENERATING:  # pragma: no cover - caller only asks for these
            raise status.StaleState(f"site v{row.version} is {row.status}")
        _audit(
            session,
            locked,
            "site.generation_started",
            f"Generating content for site v{row.version} (attempt {row.generation_attempt})",
            f"{locked.id}:site:v{row.version}:gen{row.generation_attempt}:started",
            row.version,
            run_id=run_id,
        )
        return "generate", row.id, row.version

    return checkpoint(session, business.id, S.DEPLOYING, apply)


def _generate(
    session: Session,
    business: Business,
    claim: Claim,
    ctx: DeployContext,
    version_id: uuid.UUID,
    run_id: str,
) -> None:
    try:
        content = generate_site_content(
            business, business_name=claim.name, run_id=run_id, run_llm=ctx.run_llm
        )
    except (status.StaleState, status.LeaseLost):
        raise
    except Exception as exc:
        # The call finished (its usage is recorded), so this is a normal failure, not a crash:
        # mark the attempt abandoned so only an explicit retry generates again.
        error = f"Content generation failed: {type(exc).__name__}"

        def mark(locked: Business) -> None:
            row = _row(session, version_id)
            if row.status == V.GENERATING and row.generation_run_id == run_id:
                row.status = V.ABANDONED
                row.error = error

        try:
            checkpoint(session, business.id, S.DEPLOYING, mark)
        except (status.StaleState, status.LeaseLost):
            session.rollback()
        raise

    def store(locked: Business) -> None:
        row = _row(session, version_id)
        if row.status != V.GENERATING or row.generation_run_id != run_id:
            raise status.StaleState(f"site v{row.version} is no longer ours to fill")
        row.content_json = content.model_dump(mode="json")
        row.content_hash = content_hash(content)
        row.status = V.DRAFT
        row.error = None
        _audit(
            session,
            locked,
            "site.content_generated",
            f"Content generated for site v{row.version}",
            f"{locked.id}:site:v{row.version}:content",
            row.version,
            content_hash=row.content_hash,
        )

    checkpoint(session, business.id, S.DEPLOYING, store)


# --- step 2: build -------------------------------------------------------------------------


def _render(sv: SiteVersion, business: Business, claim: Claim, ctx: DeployContext) -> RenderedSite:
    content = WebsiteContent.model_validate(sv.content_json)
    return render_site(
        content,
        business_name=claim.name,
        host=claim.host,
        price_guess=business.price_guess,
        links=ctx.links,
    )


def _build(
    session: Session, business: Business, claim: Claim, ctx: DeployContext, sv: SiteVersion
) -> None:
    site = _render(sv, business, claim, ctx)
    ref = ctx.provider.publish(claim.host, sv.version, site)
    version_id = sv.id

    def record(locked: Business) -> None:
        row = _row(session, version_id)
        if row.status != V.DRAFT:
            raise status.StaleState(f"site v{row.version} is {row.status}, expected draft")
        row.artifact_hash = site.artifact_hash
        row.manifest_json = site.manifest
        row.template_version = TEMPLATE_VERSION
        row.path = f"{claim.host}/releases/v{row.version}"
        row.status = V.BUILT
        _audit(
            session,
            locked,
            "site.built",
            f"Built immutable release v{row.version}",
            f"{locked.id}:site:v{row.version}:built",
            row.version,
            artifact_hash=site.artifact_hash,
            created=ref.created,
            provider=ctx.provider.name,
        )

    checkpoint(session, business.id, S.DEPLOYING, record)


def _ensure_published(
    business: Business, claim: Claim, ctx: DeployContext, sv: SiteVersion
) -> None:
    """A built/failed version reuses its release; re-publish only if storage lost it."""
    if sv.artifact_hash and ctx.provider.has_release(claim.host, sv.version, sv.artifact_hash):
        return
    site = _render(sv, business, claim, ctx)
    if site.artifact_hash != sv.artifact_hash:
        raise DeployError(
            f"Release v{sv.version} is missing and re-rendering produced different bytes "
            "(site settings changed); it cannot be restored exactly."
        )
    ctx.provider.publish(claim.host, sv.version, site)


# --- step 3: deploy ------------------------------------------------------------------------


def _start_deployment(
    session: Session,
    business: Business,
    claim: Claim,
    ctx: DeployContext,
    sv: SiteVersion,
    expected: BusinessStatus = S.DEPLOYING,
) -> tuple[uuid.UUID, int]:
    version_id = sv.id

    def apply(locked: Business) -> tuple[uuid.UUID, int]:
        stale = session.scalars(
            select(Deployment).where(
                Deployment.business_id == locked.id,
                Deployment.status.in_([DeploymentStatus.STARTED, DeploymentStatus.ACTIVATED]),
            )
        ).all()
        for dep in stale:  # left behind by a crashed attempt
            dep.status = DeploymentStatus.FAILED
            dep.error = "Interrupted before verification (worker crash or lost lease)"
            dep.finished_at = func.now()
        session.flush()
        attempt = (
            session.scalar(
                select(func.max(Deployment.attempt)).where(Deployment.site_version_id == version_id)
            )
            or 0
        ) + 1
        dep = Deployment(
            business_id=locked.id,
            site_version_id=version_id,
            host=claim.host,
            provider=ctx.provider.name,
            attempt=attempt,
            status=DeploymentStatus.STARTED,
        )
        session.add(dep)
        session.flush()
        return dep.id, attempt

    return checkpoint(session, business.id, expected, apply)


def _finish_failed(
    session: Session,
    business: Business,
    sv: SiteVersion,
    dep_id: uuid.UUID,
    attempt: int,
    error: str,
    *,
    health: dict | None = None,
    level: str | None = None,
    restored: int | None = None,
    expected: BusinessStatus = S.DEPLOYING,
) -> None:
    version_id, version = sv.id, sv.version

    def apply(locked: Business) -> None:
        dep = session.get(Deployment, dep_id, with_for_update=True, populate_existing=True)
        dep.status = DeploymentStatus.FAILED
        dep.error = error[:2000]
        dep.verification_level = level
        dep.health_json = (health or {}) | {"restored_version": restored}
        dep.finished_at = func.now()
        row = _row(session, version_id)
        row.status = V.FAILED
        row.error = error[:2000]
        _audit(
            session,
            locked,
            "deploy.failed",
            error[:2000],
            f"{locked.id}:deploy:v{version}:a{attempt}:failed",
            version,
            attempt=attempt,
            restored_version=restored,
        )

    checkpoint(session, business.id, expected, apply)


def _restore_previous(ctx: DeployContext, claim: Claim, previous: SiteVersion | None) -> int | None:
    """Re-activate the last verified release (atomic symlink swap). Returns its version."""
    if previous is None:
        return None
    try:
        if ctx.provider.active_version(claim.host) != previous.version:
            ctx.provider.activate(claim.host, previous.version)
        return previous.version
    except Exception:
        logger.exception("could not restore %s v%s", claim.host, previous.version)
        return None


def _deploy(
    session: Session,
    business: Business,
    claim: Claim,
    ctx: DeployContext,
    sv: SiteVersion,
    expected: BusinessStatus = S.DEPLOYING,
) -> StageResult:
    """Activate, verify, and on failure restore the previous verified release (M4B).

    `expected` is the business status the checkpoints require. M4 uses the default (DEPLOYING;
    the final writes then commit with the runner's DEPLOYING -> LIVE transition). M6 promotion
    and manual rollback call it with LIVE on a live business and commit the session themselves.
    """
    previous = deployed_version(session, business.id, exclude=sv.id)
    dep_id, attempt = _start_deployment(session, business, claim, ctx, sv, expected)
    try:
        ctx.provider.activate(claim.host, sv.version)
    except Exception as exc:
        restored = _restore_previous(ctx, claim, previous)
        msg = f"Activation of v{sv.version} failed: {type(exc).__name__}"
        _finish_failed(
            session, business, sv, dep_id, attempt, msg, restored=restored, expected=expected
        )
        raise DeploymentFailed(msg) from exc

    def activated(locked: Business) -> None:
        # Hold the lease for the whole verification (DNS + HTTPS share one deadline).
        locked.locked_until = func.now() + timedelta(seconds=ctx.verify_lease_s)
        dep = session.get(Deployment, dep_id, with_for_update=True, populate_existing=True)
        dep.status = DeploymentStatus.ACTIVATED
        _audit(
            session,
            locked,
            "deploy.activated",
            f"Activated v{sv.version} on {claim.host}",
            f"{locked.id}:deploy:v{sv.version}:a{attempt}:activated",
            sv.version,
            attempt=attempt,
        )

    checkpoint(session, business.id, expected, activated)

    report = ctx.provider.verify(claim.host, sv.version, sv.manifest_json)
    level = report.level.value if report.level else None
    health = report.to_json()
    if not acceptable_for_live(report, require_public=ctx.require_public):
        if report.ok:
            msg = (
                f"Verification level '{level}' does not meet the required 'public' level "
                "(SITE_REQUIRE_PUBLIC)."
            )
        else:
            msg = f"Health check failed for v{sv.version}: {report.error}"
        restored = _restore_previous(ctx, claim, previous)
        _finish_failed(
            session,
            business,
            sv,
            dep_id,
            attempt,
            msg,
            health=health,
            level=level,
            restored=restored,
            expected=expected,
        )
        raise DeploymentFailed(msg)

    version_id = sv.id

    def verified(locked: Business) -> None:
        dep = session.get(Deployment, dep_id, with_for_update=True, populate_existing=True)
        dep.status = DeploymentStatus.VERIFIED
        dep.verification_level = level
        dep.health_json = health
        dep.finished_at = func.now()
        row = _row(session, version_id)
        row.status = V.DEPLOYED
        row.deployed_at = func.now()
        row.error = None
        for old in session.scalars(
            select(SiteVersion).where(
                SiteVersion.business_id == locked.id,
                SiteVersion.status == V.DEPLOYED,
                SiteVersion.id != version_id,
            )
        ):
            old.status = V.SUPERSEDED
        _audit(
            session,
            locked,
            "deploy.verified",
            f"v{row.version} verified on {claim.host} ({level})",
            f"{locked.id}:deploy:v{row.version}:a{attempt}:verified",
            row.version,
            attempt=attempt,
            verification_level=level,
            publicly_reachable=health["publicly_reachable"],
        )

    # Not committed here: these writes commit atomically with the runner's DEPLOYING -> LIVE
    # transition, so a lost lease can never leave a verified version on a DEPLOYING business.
    checkpoint(session, business.id, expected, verified, commit=False)
    public = "publicly reachable" if health["publicly_reachable"] else "NOT publicly verified"
    return StageResult(
        S.LIVE,
        f"Site v{sv.version} live at {claim.host} ({level} verification; {public})",
        version_ref=f"site-v{sv.version}",
        # One DEPLOYING -> LIVE per site version: v2 does not collide with v1, while a
        # duplicate execution for the same version is still refused by the key.
        key_ref=f"site:{version_id}",
    )


# --- the stage -------------------------------------------------------------------------------


def run_deploy_stage(session: Session, business: Business, ctx: DeployContext) -> StageResult:
    """DEPLOYING handler body. Raises on failure (the runner records FAILED)."""
    run_id = current_run_id.get()
    claim = load_claim(session, business)
    sv = open_version(session, business.id)

    if sv is None or sv.status in (V.GENERATING, V.ABANDONED):
        outcome, version_id, version = _start_generation(session, business, claim, run_id)
        if outcome == "abandoned":
            raise GenerationAbandoned(
                f"Content generation for site v{version} was interrupted earlier. "
                "Retry to generate it again (a new paid model call)."
            )
        _generate(session, business, claim, ctx, version_id, run_id)
        sv = open_version(session, business.id)

    if sv.host != claim.host:
        raise DeployInputError(f"Site v{sv.version} was built for a different host.")
    if sv.status == V.DRAFT:
        _build(session, business, claim, ctx, sv)
        sv = open_version(session, business.id)
    else:  # built or failed: the immutable release already exists (or is restored)
        _ensure_published(business, claim, ctx, sv)

    return _deploy(session, business, claim, ctx, sv)


def make_context(
    run_llm: RunLLM,
    *,
    provider_factory: Callable[[], DeployProvider] | None = None,
    links: SiteLinks | None = None,
    require_public: bool | None = None,
) -> DeployContext:
    from app.deploy.site_model import site_links

    settings = DeploySettings.from_config()
    provider = (provider_factory or get_provider)()
    return DeployContext(
        provider=provider,
        links=links or site_links(config.WAITLIST_POST_URL, config.ABUSE_REPORT_EMAIL),
        require_public=settings.require_public if require_public is None else require_public,
        run_llm=run_llm,
        verify_lease_s=config.WORKER_CLAIM_TTL_SECONDS + settings.health_timeout_s + 30,
    )


# --- API side ------------------------------------------------------------------------------


@dataclass(frozen=True)
class DeployRequest:
    business: Business
    changed: bool  # False: already deploying or live (idempotent repeat)


def request_deploy(
    session: Session,
    business_id: uuid.UUID,
    *,
    actor: Actor | None = None,
    settings: DeploySettings | None = None,
) -> DeployRequest:
    """DOMAIN_PENDING -> DEPLOYING under the row lock. Commits.

    Refuses (DeployNotConfigured / DeployConfigError) before changing anything if deployment is
    off or unsafe. Concurrent requests serialise on the row lock: exactly one transitions; the
    rest see DEPLOYING and return changed=False. FAILED businesses use /retry instead.
    """
    get_provider(settings)  # validates configuration; no side effects
    business = status.lock_business(session, business_id)
    current = S(business.status)
    if current in (S.DEPLOYING, S.LIVE):
        session.rollback()
        return DeployRequest(business, False)
    if current != S.DOMAIN_PENDING:
        session.rollback()
        raise NotEligible(f"Business is {current}; deployment needs a claimed name first.")
    claim = load_claim(session, business)
    status.transition(
        session,
        business_id,
        S.DEPLOYING,
        actor=actor or Actor.user(),
        action="deploy.requested",
        reason=f"Deployment requested for {claim.host}",
        expected_from=S.DOMAIN_PENDING,
    )
    session.commit()
    return DeployRequest(business, True)
