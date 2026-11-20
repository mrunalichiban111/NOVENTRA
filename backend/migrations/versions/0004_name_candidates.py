"""name candidates, subdomain claims and per-TLD domain checks (M4A: FR-BR-1..5)

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-19 20:00:00

Upgrade keeps every pre-M4 domain_candidates row and column in place (tld/available/price/
score become legacy; tld becomes nullable). Legacy rows get slug = lower(name), valid=false,
invalid_reason='legacy_unvalidated'. Subdomain claims are enforced by two partial unique
indexes on selected rows only.

Downgrade folds M4 data back into the M1 shape:
  1. score = coalesce(score, score_total).
  2. Each domain_checks row becomes one (business_id, name, tld) row (AVAILABLE -> true,
     UNAVAILABLE -> false, UNKNOWN -> null; raw_price -> price when it fits numeric(12,2)).
     When several candidates map to the same key, the selected one wins, then the lowest
     rank, then the lowest id. An existing row with that key always wins.
  3. M4 candidates (tld NULL) that had checks are now represented by those rows and are
     removed; those without checks keep one row with tld '' (duplicates by the same order).
  4. domain_checks, the new indexes and columns are dropped and tld is NOT NULL again.
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0004'
down_revision = '0003'
branch_labels = None
depends_on = None

_ORDER = "c.selected DESC, c.rank ASC NULLS LAST, c.id ASC"


def upgrade() -> None:
    op.alter_column('domain_candidates', 'tld', existing_type=sa.String(length=24), nullable=True)
    op.add_column('domain_candidates', sa.Column('run_id', sa.String(length=64), nullable=True))
    op.add_column('domain_candidates', sa.Column('slug', sa.String(length=128), nullable=True))
    op.add_column('domain_candidates', sa.Column('valid', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.add_column('domain_candidates', sa.Column('invalid_reason', sa.String(length=32), nullable=True))
    op.add_column('domain_candidates', sa.Column('rank', sa.Integer(), nullable=True))
    op.add_column('domain_candidates', sa.Column('score_total', sa.Numeric(precision=6, scale=2), nullable=True))
    op.add_column('domain_candidates', sa.Column('score_factors', postgresql.JSONB(astext_type=sa.Text()), server_default='{}', nullable=False))
    op.add_column('domain_candidates', sa.Column('rationale', sa.Text(), nullable=True))
    op.add_column('domain_candidates', sa.Column('selected', sa.Boolean(), server_default=sa.text('false'), nullable=False))
    op.execute(
        "UPDATE domain_candidates SET slug = lower(name), valid = false, "
        "invalid_reason = 'legacy_unvalidated' WHERE slug IS NULL"
    )
    op.create_index('uq_domain_candidates_claimed_slug', 'domain_candidates', ['slug'], unique=True, postgresql_where=sa.text('selected'))
    op.create_index('uq_domain_candidates_one_selected', 'domain_candidates', ['business_id'], unique=True, postgresql_where=sa.text('selected'))
    op.create_index('ix_domain_candidates_business_rank', 'domain_candidates', ['business_id', 'rank'], unique=False)

    op.create_table('domain_checks',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('candidate_id', sa.Uuid(), nullable=False),
    sa.Column('tld', sa.String(length=24), nullable=False),
    sa.Column('status', sa.String(length=12), nullable=False),
    sa.Column('reason', sa.String(length=32), nullable=True),
    sa.Column('raw_price', sa.Numeric(precision=20, scale=4), nullable=True),
    sa.Column('currency', sa.String(length=8), nullable=True),
    sa.Column('definitive', sa.Boolean(), nullable=True),
    sa.Column('checked_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.CheckConstraint("status IN ('AVAILABLE', 'UNAVAILABLE', 'UNKNOWN')", name='ck_domain_checks_status'),
    sa.ForeignKeyConstraint(['candidate_id'], ['domain_candidates.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('candidate_id', 'tld', name='uq_domain_checks_candidate_tld')
    )


def downgrade() -> None:
    op.execute("UPDATE domain_candidates SET score = coalesce(score, score_total)")
    # 2. one legacy row per (business, name, tld) from the per-TLD checks
    op.execute(f"""
        INSERT INTO domain_candidates (id, business_id, name, tld, available, price, score, created_at)
        SELECT DISTINCT ON (c.business_id, c.name, k.tld)
               gen_random_uuid(), c.business_id, c.name, k.tld,
               CASE k.status WHEN 'AVAILABLE' THEN true WHEN 'UNAVAILABLE' THEN false END,
               CASE WHEN k.raw_price < 10000000000 THEN round(k.raw_price, 2) END,
               c.score, c.created_at
        FROM domain_checks k JOIN domain_candidates c ON c.id = k.candidate_id
        WHERE c.tld IS NULL
        ORDER BY c.business_id, c.name, k.tld, {_ORDER}
        ON CONFLICT ON CONSTRAINT uq_domain_candidates_business_name_tld DO NOTHING
    """)
    # 3. M4 candidates: drop those now represented by check rows, then dedupe the rest
    op.execute("""
        DELETE FROM domain_candidates c
        WHERE c.tld IS NULL AND EXISTS (SELECT 1 FROM domain_checks k WHERE k.candidate_id = c.id)
    """)
    op.execute(f"""
        DELETE FROM domain_candidates c
        WHERE c.tld IS NULL AND (
            EXISTS (SELECT 1 FROM domain_candidates o
                    WHERE o.business_id = c.business_id AND o.name = c.name AND o.tld = '')
            OR c.id <> (SELECT c2.id FROM domain_candidates c2
                        WHERE c2.business_id = c.business_id AND c2.name = c.name
                          AND c2.tld IS NULL
                        ORDER BY {_ORDER.replace('c.', 'c2.')} LIMIT 1)
        )
    """)
    op.execute("UPDATE domain_candidates SET tld = '' WHERE tld IS NULL")

    op.drop_table('domain_checks')
    op.drop_index('ix_domain_candidates_business_rank', table_name='domain_candidates')
    op.drop_index('uq_domain_candidates_one_selected', table_name='domain_candidates')
    op.drop_index('uq_domain_candidates_claimed_slug', table_name='domain_candidates')
    for column in ('selected', 'rationale', 'score_factors', 'score_total', 'rank',
                   'invalid_reason', 'valid', 'slug', 'run_id'):
        op.drop_column('domain_candidates', column)
    op.alter_column('domain_candidates', 'tld', existing_type=sa.String(length=24), nullable=False)
