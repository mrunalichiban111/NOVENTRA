"""SQLAlchemy models for the core SRS entities (SRS section 5).

Status/outcome columns are strings guarded by CHECK constraints rather than
Postgres enums, so they can evolve through ordinary migrations. No secrets are
stored in the database.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class BusinessStatus(StrEnum):
    INTAKE = "INTAKE"
    PREMORTEM = "PREMORTEM"
    GATED = "GATED"
    DOMAIN_PENDING = "DOMAIN_PENDING"
    DEPLOYING = "DEPLOYING"
    LIVE = "LIVE"
    TESTING = "TESTING"
    IMPROVING = "IMPROVING"
    DONE = "DONE"
    REJECTED = "REJECTED"
    FAILED = "FAILED"


def _in(column: str, values: list[str]) -> str:
    quoted = ", ".join(f"'{v}'" for v in values)
    return f"{column} IN ({quoted})"


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, primary_key=True, default=uuid.uuid4)


def _created_at() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _business_fk(ondelete: str = "CASCADE") -> Mapped[uuid.UUID]:
    return mapped_column(Uuid, ForeignKey("businesses.id", ondelete=ondelete), nullable=False)


class ContactMessage(Base):
    __tablename__ = "contact_messages"
    __table_args__ = (Index("ix_contact_messages_ip_created_at", "ip", "created_at"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    ip: Mapped[str] = mapped_column(String(45), nullable=False)
    created_at: Mapped[datetime] = _created_at()
    sent_ok: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))


class Business(Base):
    __tablename__ = "businesses"
    __table_args__ = (
        CheckConstraint(
            _in("status", [s.value for s in BusinessStatus]), name="ck_businesses_status"
        ),
        CheckConstraint(
            "price_guess IS NULL OR price_guess >= 0", name="ck_businesses_price_guess"
        ),
        CheckConstraint(
            "starting_budget IS NULL OR starting_budget >= 0", name="ck_businesses_starting_budget"
        ),
        CheckConstraint("attempts >= 0", name="ck_businesses_attempts"),
        Index("ix_businesses_status", "status"),
        Index("ix_businesses_claim", "status", "locked_until"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    idea_text: Mapped[str] = mapped_column(Text, nullable=False)
    market: Mapped[str] = mapped_column(String(80), nullable=False, server_default="India")
    price_guess: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    starting_budget: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    target_customer: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(20), nullable=False, server_default="INTAKE")
    domain: Mapped[str | None] = mapped_column(String(255), unique=True)
    # M3: validated pre-mortem output plus Python-added labels; safety rejection reason.
    premortem_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    rejection_reason: Mapped[str | None] = mapped_column(Text)

    # Pipeline bookkeeping: retry information and the worker claim lease.
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    failed_stage: Mapped[str | None] = mapped_column(String(20))
    locked_by: Mapped[str | None] = mapped_column(String(64))
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = _created_at()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    audit_entries: Mapped[list["AuditLog"]] = relationship(
        back_populates="business", order_by="AuditLog.id", passive_deletes="all"
    )


class Assumption(Base):
    __tablename__ = "assumptions"
    __table_args__ = (
        UniqueConstraint("business_id", "key", name="uq_assumptions_business_key"),
        CheckConstraint(_in("source", ["llm", "user", "panel"]), name="ck_assumptions_source"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = _business_fk()
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    low: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    base: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    high: Mapped[Decimal | None] = mapped_column(Numeric(18, 4))
    rationale: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class Scorecard(Base):
    __tablename__ = "scorecards"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "version", "stage", name="uq_scorecards_business_version_stage"
        ),
        CheckConstraint(_in("stage", ["pre", "post"]), name="ck_scorecards_stage"),
        CheckConstraint(
            _in("verdict", ["go", "go_with_changes", "rethink"]), name="ck_scorecards_verdict"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = _business_fk()
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    stage: Mapped[str] = mapped_column(String(4), nullable=False)
    demand: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    economics: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    risk: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    overall: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    verdict: Mapped[str] = mapped_column(String(20), nullable=False)
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    created_at: Mapped[datetime] = _created_at()


class DomainCandidate(Base):
    """A generated business-name candidate (M4: FR-BR-1, FR-BR-3, FR-BR-5).

    The claim on a subdomain is canonical here: `selected=true` rows are globally unique by
    slug and unique per business (partial unique indexes). `tld`, `available`, `price` and
    `score` are pre-M4 columns kept for compatibility; per-TLD results live in DomainCheck.
    """

    __tablename__ = "domain_candidates"
    __table_args__ = (
        UniqueConstraint(
            "business_id", "name", "tld", name="uq_domain_candidates_business_name_tld"
        ),
        Index(
            "uq_domain_candidates_claimed_slug",
            "slug",
            unique=True,
            postgresql_where=text("selected"),
        ),
        Index(
            "uq_domain_candidates_one_selected",
            "business_id",
            unique=True,
            postgresql_where=text("selected"),
        ),
        Index("ix_domain_candidates_business_rank", "business_id", "rank"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = _business_fk()
    name: Mapped[str] = mapped_column(String(63), nullable=False)
    tld: Mapped[str | None] = mapped_column(String(24))  # legacy (pre-M4)
    available: Mapped[bool | None] = mapped_column()  # legacy (pre-M4)
    price: Mapped[Decimal | None] = mapped_column(Numeric(12, 2))  # legacy (pre-M4)
    score: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))  # legacy (pre-M4)
    run_id: Mapped[str | None] = mapped_column(String(64))
    slug: Mapped[str | None] = mapped_column(String(128))  # always set for M4 rows
    valid: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    invalid_reason: Mapped[str | None] = mapped_column(String(32))
    rank: Mapped[int | None] = mapped_column(Integer)
    score_total: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    score_factors: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    rationale: Mapped[str | None] = mapped_column(Text)
    selected: Mapped[bool] = mapped_column(nullable=False, server_default=text("false"))
    created_at: Mapped[datetime] = _created_at()

    checks: Mapped[list["DomainCheck"]] = relationship(
        back_populates="candidate", order_by="DomainCheck.tld", passive_deletes=True
    )


class DomainCheck(Base):
    """Last real-domain availability result per candidate and TLD (FR-BR-2, read-only).

    `raw_price` is stored exactly as the registrar returned it; its units are unverified, so it
    is never converted or shown as a guaranteed price. UNKNOWN is never treated as AVAILABLE.
    """

    __tablename__ = "domain_checks"
    __table_args__ = (
        UniqueConstraint("candidate_id", "tld", name="uq_domain_checks_candidate_tld"),
        CheckConstraint(
            _in("status", ["AVAILABLE", "UNAVAILABLE", "UNKNOWN"]), name="ck_domain_checks_status"
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("domain_candidates.id", ondelete="CASCADE"), nullable=False
    )
    tld: Mapped[str] = mapped_column(String(24), nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(32))
    raw_price: Mapped[Decimal | None] = mapped_column(Numeric(20, 4))
    currency: Mapped[str | None] = mapped_column(String(8))
    definitive: Mapped[bool | None] = mapped_column()
    checked_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    candidate: Mapped[DomainCandidate] = relationship(back_populates="checks")


class SiteVersionStatus(StrEnum):
    """Lifecycle of one site version (M4B, FR-SITE-3).

    generating -> draft (content stored) -> built (immutable release written) -> deployed.
    A crashed generation becomes abandoned; a failed health check leaves it failed (artifact
    kept, retry reuses it). deployed -> superseded / rolled_back happen in M6.
    """

    GENERATING = "generating"
    ABANDONED = "abandoned"
    DRAFT = "draft"
    BUILT = "built"
    FAILED = "failed"
    DEPLOYED = "deployed"
    SUPERSEDED = "superseded"
    ROLLED_BACK = "rolled_back"


# At most one unfinished version per business (partial unique index uq_site_versions_one_open).
SITE_OPEN_STATUSES: tuple[str, ...] = (
    SiteVersionStatus.GENERATING,
    SiteVersionStatus.ABANDONED,
    SiteVersionStatus.DRAFT,
    SiteVersionStatus.BUILT,
    SiteVersionStatus.FAILED,
)
_SITE_OPEN_SQL = _in("status", list(SITE_OPEN_STATUSES))


class SiteVersion(Base):
    """One generated version of a business's site. Artifacts live on disk, not here.

    Content may be absent only while generating (or after that generation was abandoned).
    """

    __tablename__ = "site_versions"
    __table_args__ = (
        UniqueConstraint("business_id", "version", name="uq_site_versions_business_version"),
        CheckConstraint("version >= 1", name="ck_site_versions_version"),
        CheckConstraint(
            _in("status", [s.value for s in SiteVersionStatus]),
            name="ck_site_versions_status",
        ),
        CheckConstraint(
            "status IN ('generating', 'abandoned') "
            "OR (content_json IS NOT NULL AND content_hash IS NOT NULL)",
            name="ck_site_versions_content",
        ),
        CheckConstraint("generation_attempt >= 0", name="ck_site_versions_generation_attempt"),
        Index(
            "uq_site_versions_one_open",
            "business_id",
            unique=True,
            postgresql_where=text(_SITE_OPEN_SQL),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = _business_fk()
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    content_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    content_hash: Mapped[str | None] = mapped_column(String(64))
    path: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, server_default="draft")
    parent_version: Mapped[int | None] = mapped_column(Integer)
    deployed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()
    # M4B: host the version was built for, renderer version, and the immutable artifact.
    host: Mapped[str | None] = mapped_column(String(255))
    template_version: Mapped[str | None] = mapped_column(String(16))
    artifact_hash: Mapped[str | None] = mapped_column(String(64))
    manifest_json: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    error: Mapped[str | None] = mapped_column(Text)
    # M4B crash recovery: which run started the (paid) generation call, and how often.
    generation_run_id: Mapped[str | None] = mapped_column(String(64))
    generation_attempt: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    generation_started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class DeploymentStatus(StrEnum):
    STARTED = "started"
    ACTIVATED = "activated"
    VERIFIED = "verified"
    FAILED = "failed"


class VerificationLevel(StrEnum):
    """How strongly a deployment was verified (M4B R1). Only `public` means reachable online."""

    SIMULATED = "simulated"  # mock provider
    LOCAL = "local"  # files and hashes verified on disk; DNS/HTTPS not checked
    PUBLIC = "public"  # DNS resolved, HTTPS 200, served body hash matches the manifest


class Deployment(Base):
    """Deployment history: one row per activation attempt of a site version (FR-SITE-3/4).

    Only one deployment per business may be in flight (started/activated) at a time.
    """

    __tablename__ = "deployments"
    __table_args__ = (
        UniqueConstraint("site_version_id", "attempt", name="uq_deployments_version_attempt"),
        CheckConstraint(
            _in("status", [s.value for s in DeploymentStatus]), name="ck_deployments_status"
        ),
        CheckConstraint(
            "verification_level IS NULL OR "
            + _in("verification_level", [v.value for v in VerificationLevel]),
            name="ck_deployments_verification_level",
        ),
        CheckConstraint("attempt >= 1", name="ck_deployments_attempt"),
        CheckConstraint(
            "status <> 'verified' OR verification_level IS NOT NULL",
            name="ck_deployments_verified_level",
        ),
        Index("ix_deployments_business_started", "business_id", "started_at"),
        Index(
            "uq_deployments_one_in_flight",
            "business_id",
            unique=True,
            postgresql_where=text("status IN ('started', 'activated')"),
        ),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = _business_fk()
    site_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("site_versions.id", ondelete="CASCADE"), nullable=False
    )
    host: Mapped[str] = mapped_column(String(255), nullable=False)
    provider: Mapped[str] = mapped_column(String(16), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(12), nullable=False, server_default="started")
    verification_level: Mapped[str | None] = mapped_column(String(12))
    health_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PersonaPanel(Base):
    __tablename__ = "persona_panels"

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = _business_fk()
    personas_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = _created_at()


class PersonaRun(Base):
    __tablename__ = "persona_runs"
    __table_args__ = (
        UniqueConstraint(
            "panel_id",
            "site_version_id",
            "persona_id",
            "run_idx",
            name="uq_persona_runs_evaluation",
        ),
        CheckConstraint(_in("decision", ["convert", "bounce"]), name="ck_persona_runs_decision"),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="ck_persona_runs_confidence"),
        Index("ix_persona_runs_site_version", "site_version_id"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    panel_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("persona_panels.id", ondelete="CASCADE"), nullable=False
    )
    site_version_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("site_versions.id", ondelete="CASCADE"), nullable=False
    )
    persona_id: Mapped[str] = mapped_column(String(64), nullable=False)
    run_idx: Mapped[int] = mapped_column(Integer, nullable=False)
    decision: Mapped[str] = mapped_column(String(8), nullable=False)
    confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))
    objection: Mapped[str | None] = mapped_column(Text)
    section: Mapped[str | None] = mapped_column(String(64))
    reasoning: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created_at()


class ImprovementAttempt(Base):
    __tablename__ = "improvement_attempts"
    __table_args__ = (
        CheckConstraint(_in("outcome", ["accepted", "rolled_back"]), name="ck_improvement_outcome"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = _business_fk()
    from_version: Mapped[int] = mapped_column(Integer, nullable=False)
    to_version: Mapped[int] = mapped_column(Integer, nullable=False)
    clusters_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    changes_json: Mapped[list[Any]] = mapped_column(JSONB, nullable=False, server_default="[]")
    delta_demand: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    outcome: Mapped[str | None] = mapped_column(String(12))
    created_at: Mapped[datetime] = _created_at()


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            _in("status", ["pending", "approved", "rejected"]), name="ck_approvals_status"
        ),
        Index("ix_approvals_business_status", "business_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = _business_fk()
    action_type: Mapped[str] = mapped_column(String(64), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    status: Mapped[str] = mapped_column(String(10), nullable=False, server_default="pending")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created_at()


class AuditLog(Base):
    """Append-only record of every automated action (FR-GT-2).

    The business FK is RESTRICT: audit history must never vanish with a business.
    `idempotency_key` makes repeated worker execution a no-op.
    """

    __tablename__ = "audit_log"
    __table_args__ = (Index("ix_audit_log_business_ts", "business_id", "ts"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=False), primary_key=True)
    business_id: Mapped[uuid.UUID] = _business_fk(ondelete="RESTRICT")
    actor: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text)
    version_ref: Mapped[str | None] = mapped_column(String(64))
    from_status: Mapped[str | None] = mapped_column(String(20))
    to_status: Mapped[str | None] = mapped_column(String(20))
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, server_default="{}")
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    business: Mapped[Business] = relationship(back_populates="audit_entries")


class WaitlistSignup(Base):
    __tablename__ = "waitlist_signups"
    __table_args__ = (UniqueConstraint("business_id", "email", name="uq_waitlist_business_email"),)

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID] = _business_fk()
    email: Mapped[str] = mapped_column(String(320), nullable=False)
    created_at: Mapped[datetime] = _created_at()


class LlmUsage(Base):
    """One row per real model call whose usage is known (M2). Costs are ESTIMATES from the
    price registry in app/llm/pricing.py, not billing data. Written in its own transaction
    so a later pipeline-stage failure cannot erase it."""

    __tablename__ = "llm_usage"
    __table_args__ = (
        CheckConstraint(_in("status", ["success", "failure"]), name="ck_llm_usage_status"),
        Index("ix_llm_usage_run_id", "run_id"),
        Index("ix_llm_usage_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    business_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("businesses.id", ondelete="SET NULL")
    )
    run_id: Mapped[str | None] = mapped_column(String(64))
    task: Mapped[str] = mapped_column(String(64), nullable=False)
    provider: Mapped[str] = mapped_column(String(32), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cache_read_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    cache_write_tokens: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    estimated_cost_usd: Mapped[Decimal] = mapped_column(
        Numeric(12, 6), nullable=False, server_default="0"
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    status: Mapped[str] = mapped_column(String(8), nullable=False)
    error_category: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[datetime] = _created_at()


class LlmBudgetReservation(Base):
    """Worst-case spend held for an in-flight call. Active, unexpired rows count toward the
    per-run cap; expiry is the only crash recovery."""

    __tablename__ = "llm_budget_reservations"
    __table_args__ = (
        CheckConstraint(
            _in("status", ["active", "settled", "released"]), name="ck_llm_reservations_status"
        ),
        Index("ix_llm_reservations_run_status", "run_id", "status"),
    )

    id: Mapped[uuid.UUID] = _uuid_pk()
    run_id: Mapped[str] = mapped_column(String(64), nullable=False)
    amount_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False)
    status: Mapped[str] = mapped_column(String(8), nullable=False, server_default="active")
    created_at: Mapped[datetime] = _created_at()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
