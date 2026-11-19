"""llm usage tracking and budget reservations (M2)

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-19 12:00:00
"""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('llm_usage',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('business_id', sa.Uuid(), nullable=True),
    sa.Column('run_id', sa.String(length=64), nullable=True),
    sa.Column('task', sa.String(length=64), nullable=False),
    sa.Column('provider', sa.String(length=32), nullable=False),
    sa.Column('model', sa.String(length=80), nullable=False),
    sa.Column('attempt', sa.Integer(), nullable=False),
    sa.Column('input_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('output_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('cache_read_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('cache_write_tokens', sa.Integer(), server_default='0', nullable=False),
    sa.Column('estimated_cost_usd', sa.Numeric(precision=12, scale=6), server_default='0', nullable=False),
    sa.Column('latency_ms', sa.Integer(), server_default='0', nullable=False),
    sa.Column('status', sa.String(length=8), nullable=False),
    sa.Column('error_category', sa.String(length=32), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('success', 'failure')", name='ck_llm_usage_status'),
    sa.ForeignKeyConstraint(['business_id'], ['businesses.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_llm_usage_run_id', 'llm_usage', ['run_id'], unique=False)
    op.create_index('ix_llm_usage_created_at', 'llm_usage', ['created_at'], unique=False)
    op.create_table('llm_budget_reservations',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('run_id', sa.String(length=64), nullable=False),
    sa.Column('amount_usd', sa.Numeric(precision=12, scale=6), nullable=False),
    sa.Column('status', sa.String(length=8), server_default='active', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.CheckConstraint("status IN ('active', 'settled', 'released')", name='ck_llm_reservations_status'),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index('ix_llm_reservations_run_status', 'llm_budget_reservations', ['run_id', 'status'], unique=False)


def downgrade() -> None:
    op.drop_index('ix_llm_reservations_run_status', table_name='llm_budget_reservations')
    op.drop_table('llm_budget_reservations')
    op.drop_index('ix_llm_usage_created_at', table_name='llm_usage')
    op.drop_index('ix_llm_usage_run_id', table_name='llm_usage')
    op.drop_table('llm_usage')
