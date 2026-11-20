"""persona arena (M5) and improvement studio (M6) storage

Revision ID: 0007
Revises: 0006

Upgrade (no data is deleted; aborts if persona_runs already has rows):
  - persona_evaluations (new): one claimable evaluation job per (panel, site_version), with its
    lease, D score, standard error and the comparability metadata (FR-AR-2..4, SRS 6).
  - persona_panels: generation status/lease columns, one panel per business, personas_json
    nullable until the panel is ready (FR-AR-1).
  - persona_runs: evaluation_id (composite FK so a run cannot point at another panel/version),
    unique (evaluation_id, persona_id, run_idx) as the idempotent insert key, batch_index,
    objection_type, missing_info, model, run_id (FR-AR-6).
  - site_versions: status 'discarded' (a not-promoted M6 draft; not an open status).
  - improvement_attempts: M6 status, questions/answers/proposal JSON, source version/hash,
    evaluation and draft references; one open attempt per business (FR-IM-1..3).

Downgrade NEVER deletes data silently. If any M5/M6 rows exist (persona_panels,
persona_evaluations, persona_runs, improvement_attempts, or 'discarded' site versions) it
raises unless run with `alembic -x allow_data_loss=true downgrade 0006`. With that flag it is
DESTRUCTIVE: those rows are deleted and 'discarded' site versions become 'superseded'.
On an empty M5/M6 state the downgrade is lossless. Never run it on production without approval.
"""

import sqlalchemy as sa
from alembic import context, op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

_SV_OLD = (
    "status IN ('generating', 'abandoned', 'draft', 'built', 'failed', 'deployed', "
    "'superseded', 'rolled_back')"
)
_SV_NEW = (
    "status IN ('generating', 'abandoned', 'draft', 'built', 'failed', 'deployed', "
    "'superseded', 'rolled_back', 'discarded')"
)
_ATTEMPT_STATUSES = (
    "status IN ('awaiting_answers', 'proposed', 'approved_for_draft', 'draft_built', "
    "'draft_evaluated', 'promoted', 'not_promoted', 'rejected', 'stale', 'discarded')"
)
_ATTEMPT_TERMINAL = "status NOT IN ('promoted', 'not_promoted', 'rejected', 'stale', 'discarded')"
_OUTCOME_OLD = "outcome IN ('accepted', 'rolled_back')"
_OUTCOME_NEW = "outcome IN ('accepted', 'rolled_back', 'promoted', 'not_promoted', 'discarded')"


def _now() -> sa.TextClause:
    return sa.text("now()")


def upgrade() -> None:
    bind = op.get_bind()
    existing_runs = bind.execute(sa.text("SELECT count(*) FROM persona_runs")).scalar_one()
    if existing_runs:
        raise RuntimeError(
            f"0007 upgrade: persona_runs already has {existing_runs} rows without an evaluation; "
            "refusing to guess their evaluation_id. Nothing was changed."
        )

    # --- persona_panels -------------------------------------------------------------------
    op.alter_column("persona_panels", "personas_json", existing_type=JSONB, nullable=True)
    op.add_column(
        "persona_panels",
        sa.Column("status", sa.String(12), server_default="pending", nullable=False),
    )
    op.add_column("persona_panels", sa.Column("error", sa.Text(), nullable=True))
    op.add_column("persona_panels", sa.Column("generation_run_id", sa.String(64), nullable=True))
    op.add_column(
        "persona_panels",
        sa.Column("generation_attempt", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("persona_panels", sa.Column("model", sa.String(80), nullable=True))
    op.add_column("persona_panels", sa.Column("prompt_version", sa.String(16), nullable=True))
    op.add_column("persona_panels", sa.Column("locked_by", sa.String(64), nullable=True))
    op.add_column(
        "persona_panels", sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "persona_panels",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
    )
    # Pre-0007 panels (if any) always had personas: they are ready.
    op.execute("UPDATE persona_panels SET status = 'ready' WHERE personas_json IS NOT NULL")
    op.create_check_constraint(
        "ck_persona_panels_status",
        "persona_panels",
        "status IN ('pending', 'generating', 'ready', 'failed')",
    )
    op.create_check_constraint(
        "ck_persona_panels_ready_content",
        "persona_panels",
        "status <> 'ready' OR personas_json IS NOT NULL",
    )
    op.create_unique_constraint("uq_persona_panels_business", "persona_panels", ["business_id"])
    op.create_index("ix_persona_panels_claim", "persona_panels", ["status", "locked_until"])

    # --- persona_evaluations --------------------------------------------------------------
    op.create_table(
        "persona_evaluations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("business_id", sa.Uuid(), nullable=False),
        sa.Column("panel_id", sa.Uuid(), nullable=False),
        sa.Column("site_version_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(12), server_default="queued", nullable=False),
        sa.Column("expected_runs", sa.Integer(), server_default="90", nullable=False),
        sa.Column("evaluator_model", sa.String(80), nullable=True),
        sa.Column("prompt_version", sa.String(16), nullable=True),
        sa.Column("rubric_version", sa.String(16), nullable=True),
        sa.Column("d_score", sa.Numeric(6, 2), nullable=True),
        sa.Column("d_se", sa.Numeric(6, 2), nullable=True),
        sa.Column("aggregate_json", JSONB, server_default="{}", nullable=False),
        sa.Column("error_code", sa.String(32), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("run_id", sa.String(64), nullable=True),
        sa.Column("locked_by", sa.String(64), nullable=True),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
        sa.CheckConstraint(
            "status IN ('queued', 'running', 'complete', 'incomplete')",
            name="ck_persona_evaluations_status",
        ),
        sa.CheckConstraint("attempts >= 0", name="ck_persona_evaluations_attempts"),
        sa.CheckConstraint("expected_runs > 0", name="ck_persona_evaluations_expected"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["panel_id"], ["persona_panels.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["site_version_id"], ["site_versions.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "panel_id", "site_version_id", name="uq_persona_evaluations_panel_version"
        ),
        sa.UniqueConstraint(
            "id", "panel_id", "site_version_id", name="uq_persona_evaluations_id_panel_version"
        ),
    )
    op.create_index(
        "uq_persona_evaluations_one_running",
        "persona_evaluations",
        ["business_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )
    op.create_index(
        "ix_persona_evaluations_claim", "persona_evaluations", ["status", "locked_until"]
    )

    # --- persona_runs ---------------------------------------------------------------------
    op.add_column("persona_runs", sa.Column("evaluation_id", sa.Uuid(), nullable=False))
    op.add_column("persona_runs", sa.Column("batch_index", sa.SmallInteger(), nullable=True))
    op.add_column("persona_runs", sa.Column("objection_type", sa.String(16), nullable=True))
    op.add_column(
        "persona_runs", sa.Column("missing_info", JSONB, server_default="[]", nullable=False)
    )
    op.add_column("persona_runs", sa.Column("model", sa.String(80), nullable=True))
    op.add_column("persona_runs", sa.Column("run_id", sa.String(64), nullable=True))
    op.create_foreign_key(
        "fk_persona_runs_evaluation",
        "persona_runs",
        "persona_evaluations",
        ["evaluation_id", "panel_id", "site_version_id"],
        ["id", "panel_id", "site_version_id"],
        ondelete="CASCADE",
    )
    op.create_unique_constraint(
        "uq_persona_runs_eval_persona_repeat",
        "persona_runs",
        ["evaluation_id", "persona_id", "run_idx"],
    )
    op.create_check_constraint(
        "ck_persona_runs_objection_type",
        "persona_runs",
        "objection_type IS NULL OR objection_type IN "
        "('missing_info', 'price', 'trust', 'fit', 'clarity', 'other')",
    )
    op.create_check_constraint("ck_persona_runs_run_idx", "persona_runs", "run_idx BETWEEN 0 AND 2")
    op.create_index("ix_persona_runs_evaluation", "persona_runs", ["evaluation_id"])

    # --- site_versions --------------------------------------------------------------------
    op.drop_constraint("ck_site_versions_status", "site_versions", type_="check")
    op.create_check_constraint("ck_site_versions_status", "site_versions", _SV_NEW)

    # --- improvement_attempts (M6) --------------------------------------------------------
    op.alter_column("improvement_attempts", "to_version", existing_type=sa.Integer(), nullable=True)
    op.add_column(
        "improvement_attempts",
        sa.Column("status", sa.String(20), server_default="awaiting_answers", nullable=False),
    )
    op.add_column(
        "improvement_attempts",
        sa.Column("questions_json", JSONB, server_default="[]", nullable=False),
    )
    op.add_column(
        "improvement_attempts",
        sa.Column("answers_json", JSONB, server_default="{}", nullable=False),
    )
    op.add_column(
        "improvement_attempts",
        sa.Column("proposal_json", JSONB, server_default="{}", nullable=False),
    )
    op.add_column("improvement_attempts", sa.Column("source_version", sa.Integer(), nullable=True))
    op.add_column(
        "improvement_attempts", sa.Column("source_content_hash", sa.String(64), nullable=True)
    )
    for col, target in (
        ("original_evaluation_id", "persona_evaluations"),
        ("draft_evaluation_id", "persona_evaluations"),
        ("draft_site_version_id", "site_versions"),
        ("approval_id", "approvals"),
    ):
        op.add_column("improvement_attempts", sa.Column(col, sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"fk_improvement_attempts_{col}",
            "improvement_attempts",
            target,
            [col],
            ["id"],
            ondelete="SET NULL",
        )
    op.add_column("improvement_attempts", sa.Column("reason", sa.Text(), nullable=True))
    op.add_column(
        "improvement_attempts",
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now(), nullable=False),
    )
    # Pre-0007 attempts (if any) were finished loops: mark them by their old outcome.
    op.execute(
        "UPDATE improvement_attempts SET status = CASE WHEN outcome = 'accepted' THEN 'promoted' "
        "ELSE 'not_promoted' END"
    )
    op.create_check_constraint("ck_improvement_status", "improvement_attempts", _ATTEMPT_STATUSES)
    op.drop_constraint("ck_improvement_outcome", "improvement_attempts", type_="check")
    op.create_check_constraint("ck_improvement_outcome", "improvement_attempts", _OUTCOME_NEW)
    op.create_index(
        "uq_improvement_attempts_one_open",
        "improvement_attempts",
        ["business_id"],
        unique=True,
        postgresql_where=sa.text(_ATTEMPT_TERMINAL),
    )


def _m5_m6_counts(bind: sa.engine.Connection) -> dict[str, int]:
    queries = {
        "persona_panels": "SELECT count(*) FROM persona_panels",
        "persona_evaluations": "SELECT count(*) FROM persona_evaluations",
        "persona_runs": "SELECT count(*) FROM persona_runs",
        "improvement_attempts": "SELECT count(*) FROM improvement_attempts",
        "discarded_site_versions": "SELECT count(*) FROM site_versions WHERE status = 'discarded'",
    }
    return {name: bind.execute(sa.text(sql)).scalar_one() for name, sql in queries.items()}


def _allow_data_loss() -> bool:
    value = context.get_x_argument(as_dictionary=True).get("allow_data_loss", "")
    return value.strip().lower() in {"1", "true", "yes"}


def downgrade() -> None:
    bind = op.get_bind()
    counts = {k: v for k, v in _m5_m6_counts(bind).items() if v}
    if counts:
        if not _allow_data_loss():
            raise RuntimeError(
                f"0007 downgrade would destroy M5/M6 data: {counts}. Nothing was changed. "
                "Re-run with `alembic -x allow_data_loss=true downgrade 0006` to delete it."
            )
        # Explicitly authorised destructive path (documented in the module docstring).
        op.execute("DELETE FROM improvement_attempts")
        op.execute("DELETE FROM persona_panels")  # cascades evaluations and runs
        op.execute("UPDATE site_versions SET status = 'superseded' WHERE status = 'discarded'")

    # improvement_attempts
    op.drop_index("uq_improvement_attempts_one_open", table_name="improvement_attempts")
    op.drop_constraint("ck_improvement_outcome", "improvement_attempts", type_="check")
    op.create_check_constraint("ck_improvement_outcome", "improvement_attempts", _OUTCOME_OLD)
    op.drop_constraint("ck_improvement_status", "improvement_attempts", type_="check")
    for col in (
        "original_evaluation_id",
        "draft_evaluation_id",
        "draft_site_version_id",
        "approval_id",
    ):
        op.drop_constraint(
            f"fk_improvement_attempts_{col}", "improvement_attempts", type_="foreignkey"
        )
        op.drop_column("improvement_attempts", col)
    for col in ("updated_at", "reason", "source_content_hash", "source_version", "proposal_json",
                "answers_json", "questions_json", "status"):  # fmt: skip
        op.drop_column("improvement_attempts", col)
    op.alter_column(
        "improvement_attempts", "to_version", existing_type=sa.Integer(), nullable=False
    )

    # site_versions
    op.drop_constraint("ck_site_versions_status", "site_versions", type_="check")
    op.create_check_constraint("ck_site_versions_status", "site_versions", _SV_OLD)

    # persona_runs
    op.drop_index("ix_persona_runs_evaluation", table_name="persona_runs")
    op.drop_constraint("ck_persona_runs_run_idx", "persona_runs", type_="check")
    op.drop_constraint("ck_persona_runs_objection_type", "persona_runs", type_="check")
    op.drop_constraint("uq_persona_runs_eval_persona_repeat", "persona_runs", type_="unique")
    op.drop_constraint("fk_persona_runs_evaluation", "persona_runs", type_="foreignkey")
    for col in (
        "run_id",
        "model",
        "missing_info",
        "objection_type",
        "batch_index",
        "evaluation_id",
    ):
        op.drop_column("persona_runs", col)

    # persona_evaluations
    op.drop_index("ix_persona_evaluations_claim", table_name="persona_evaluations")
    op.drop_index("uq_persona_evaluations_one_running", table_name="persona_evaluations")
    op.drop_table("persona_evaluations")

    # persona_panels
    op.drop_index("ix_persona_panels_claim", table_name="persona_panels")
    op.drop_constraint("uq_persona_panels_business", "persona_panels", type_="unique")
    op.drop_constraint("ck_persona_panels_ready_content", "persona_panels", type_="check")
    op.drop_constraint("ck_persona_panels_status", "persona_panels", type_="check")
    for col in ("updated_at", "locked_until", "locked_by", "prompt_version", "model",
                "generation_attempt", "generation_run_id", "error", "status"):  # fmt: skip
        op.drop_column("persona_panels", col)
    op.alter_column("persona_panels", "personas_json", existing_type=JSONB, nullable=False)
