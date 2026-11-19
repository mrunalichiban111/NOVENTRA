from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from tests.conftest import alembic_config

TABLES = {
    "businesses", "assumptions", "scorecards", "domain_candidates", "site_versions",
    "persona_panels", "persona_runs", "improvement_attempts", "approvals", "audit_log",
    "waitlist_signups", "llm_usage", "llm_budget_reservations",
}  # fmt: skip


def test_single_head_and_up_down_up(db_url: str, engine) -> None:  # noqa: ANN001
    cfg = alembic_config(db_url)
    assert len(ScriptDirectory.from_config(cfg).get_heads()) == 1

    command.downgrade(cfg, "base")
    probe = create_engine(db_url)
    try:
        assert not (TABLES & set(inspect(probe).get_table_names()))
        command.upgrade(cfg, "head")
        assert TABLES <= set(inspect(probe).get_table_names())
    finally:
        probe.dispose()
