"""site generation state, immutable artifacts and deployment history (M4B: FR-SITE-1..5)

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-20 10:00:00

Upgrade:
  - site_versions: content becomes nullable (a 'generating' row exists before the paid model
    call returns). New columns for host, template/artifact hashes, manifest, error and
    generation crash-recovery bookkeeping. Status gains generating/abandoned/built/failed.
    A partial unique index allows at most one unfinished version per business.
  - deployments: one row per activation attempt, with the verification level reached.
    At most one in-flight deployment per business; (site_version_id, attempt) is unique.
Existing rows are untouched (all old statuses stay valid; new columns get defaults/NULL).

Downgrade (lossy by necessity, documented):
  1. deployments is dropped (deployment history is lost).
  2. site_versions rows without content (generating/abandoned) are deleted; they hold no
     generated content.
  3. built/failed/generating/abandoned rows become 'draft' (the pre-M4B open status).
  4. New columns, indexes and checks are dropped; content columns are NOT NULL again.
Artifacts on disk are never touched by a migration.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0005'
down_revision = '0004'
branch_labels = None
depends_on = None

_OLD_STATUSES = "status IN ('draft', 'deployed', 'superseded', 'rolled_back')"
_NEW_STATUSES = (
    "status IN ('generating', 'abandoned', 'draft', 'built', 'failed', 'deployed', "
    "'superseded', 'rolled_back')"
)
_OPEN = "status IN ('generating', 'abandoned', 'draft', 'built', 'failed')"


def upgrade() -> None:
    op.alter_column('site_versions', 'content_json', existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=True)
    op.alter_column('site_versions', 'content_hash', existing_type=sa.String(length=64), nullable=True)
    op.add_column('site_versions', sa.Column('host', sa.String(length=255), nullable=True))
    op.add_column('site_versions', sa.Column('template_version', sa.String(length=16), nullable=True))
    op.add_column('site_versions', sa.Column('artifact_hash', sa.String(length=64), nullable=True))
    op.add_column('site_versions', sa.Column('manifest_json', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))
    op.add_column('site_versions', sa.Column('error', sa.Text(), nullable=True))
    op.add_column('site_versions', sa.Column('generation_run_id', sa.String(length=64), nullable=True))
    op.add_column('site_versions', sa.Column('generation_attempt', sa.Integer(), server_default='0', nullable=False))
    op.add_column('site_versions', sa.Column('generation_started_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('site_versions', sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False))

    op.drop_constraint('ck_site_versions_status', 'site_versions', type_='check')
    op.create_check_constraint('ck_site_versions_status', 'site_versions', _NEW_STATUSES)
    op.create_check_constraint(
        'ck_site_versions_content', 'site_versions',
        "status IN ('generating', 'abandoned') "
        "OR (content_json IS NOT NULL AND content_hash IS NOT NULL)",
    )
    op.create_check_constraint('ck_site_versions_generation_attempt', 'site_versions', 'generation_attempt >= 0')
    op.create_index('uq_site_versions_one_open', 'site_versions', ['business_id'], unique=True, postgresql_where=sa.text(_OPEN))

    op.create_table('deployments',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('business_id', sa.Uuid(), nullable=False),
    sa.Column('site_version_id', sa.Uuid(), nullable=False),
    sa.Column('host', sa.String(length=255), nullable=False),
    sa.Column('provider', sa.String(length=16), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('status', sa.String(length=12), server_default='started', nullable=False),
    sa.Column('verification_level', sa.String(length=12), nullable=True),
    sa.Column('health_json', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('finished_at', sa.DateTime(timezone=True), nullable=True),
    sa.CheckConstraint("status IN ('started', 'activated', 'verified', 'failed')", name='ck_deployments_status'),
    sa.CheckConstraint("verification_level IS NULL OR verification_level IN ('simulated', 'local', 'public')", name='ck_deployments_verification_level'),
    sa.CheckConstraint('attempt >= 1', name='ck_deployments_attempt'),
    sa.CheckConstraint("status <> 'verified' OR verification_level IS NOT NULL", name='ck_deployments_verified_level'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['site_version_id'], ['site_versions.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('site_version_id', 'attempt', name='uq_deployments_version_attempt')
    )
    op.create_index('ix_deployments_business_started', 'deployments', ['business_id', 'started_at'], unique=False)
    op.create_index('uq_deployments_one_in_flight', 'deployments', ['business_id'], unique=True, postgresql_where=sa.text("status IN ('started', 'activated')"))


def downgrade() -> None:
    op.drop_index('uq_deployments_one_in_flight', table_name='deployments')
    op.drop_index('ix_deployments_business_started', table_name='deployments')
    op.drop_table('deployments')

    op.execute("DELETE FROM site_versions WHERE content_json IS NULL OR content_hash IS NULL")
    op.execute(f"UPDATE site_versions SET status = 'draft' WHERE NOT ({_OLD_STATUSES})")

    op.drop_index('uq_site_versions_one_open', table_name='site_versions')
    op.drop_constraint('ck_site_versions_generation_attempt', 'site_versions', type_='check')
    op.drop_constraint('ck_site_versions_content', 'site_versions', type_='check')
    op.drop_constraint('ck_site_versions_status', 'site_versions', type_='check')
    op.create_check_constraint('ck_site_versions_status', 'site_versions', _OLD_STATUSES)
    for column in ('updated_at', 'generation_started_at', 'generation_attempt', 'generation_run_id',
                   'error', 'manifest_json', 'artifact_hash', 'template_version', 'host'):
        op.drop_column('site_versions', column)
    op.alter_column('site_versions', 'content_hash', existing_type=sa.String(length=64), nullable=False)
    op.alter_column('site_versions', 'content_json', existing_type=postgresql.JSONB(astext_type=sa.Text()), nullable=False)
