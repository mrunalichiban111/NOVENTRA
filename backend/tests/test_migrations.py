from alembic import command
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect

from tests.conftest import alembic_config

TABLES = {
    "businesses", "assumptions", "scorecards", "domain_candidates", "site_versions",
    "persona_panels", "persona_runs", "improvement_attempts", "approvals", "audit_log",
    "waitlist_signups", "llm_usage", "llm_budget_reservations", "deployments",
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


# --- 0007: persona arena / improvement studio (M5/M6) ---------------------------------------

import uuid  # noqa: E402
from argparse import Namespace  # noqa: E402

import pytest  # noqa: E402
from sqlalchemy import text  # noqa: E402


def _seed_m5(conn, *, with_run: bool) -> dict[str, uuid.UUID]:  # noqa: ANN001
    ids = {k: uuid.uuid4() for k in ("business", "sv", "panel", "evaluation")}
    conn.execute(
        text("INSERT INTO businesses (id, idea_text, status) VALUES (:id, 'idea', 'LIVE')"),
        {"id": ids["business"]},
    )
    conn.execute(
        text(
            "INSERT INTO site_versions (id, business_id, version, content_json, content_hash, "
            "status) VALUES (:id, :b, 1, '{}', 'h', 'deployed')"
        ),
        {"id": ids["sv"], "b": ids["business"]},
    )
    conn.execute(
        text("INSERT INTO persona_panels (id, business_id, personas_json) VALUES (:id, :b, '[]')"),
        {"id": ids["panel"], "b": ids["business"]},
    )
    if with_run:
        conn.execute(
            text(
                "INSERT INTO persona_runs (id, panel_id, site_version_id, persona_id, run_idx, "
                "decision) VALUES (:id, :p, :sv, 'p01', 0, 'convert')"
            ),
            {"id": uuid.uuid4(), "p": ids["panel"], "sv": ids["sv"]},
        )
    return ids


def _wipe(conn) -> None:  # noqa: ANN001
    conn.execute(text("DELETE FROM persona_panels"))
    conn.execute(text("DELETE FROM site_versions"))
    conn.execute(text("DELETE FROM audit_log"))
    conn.execute(text("DELETE FROM businesses"))


def test_0007_upgrade_refuses_orphan_persona_runs(db_url: str, engine) -> None:  # noqa: ANN001
    cfg = alembic_config(db_url)
    command.downgrade(cfg, "0006")
    probe = create_engine(db_url)
    try:
        with probe.begin() as conn:
            _seed_m5(conn, with_run=True)
        with pytest.raises(RuntimeError, match="persona_runs already has 1 rows"):
            command.upgrade(cfg, "head")
        with probe.begin() as conn:  # nothing was changed or deleted
            assert conn.execute(text("SELECT count(*) FROM persona_runs")).scalar_one() == 1
            conn.execute(text("DELETE FROM persona_runs"))
            _wipe(conn)
    finally:
        probe.dispose()
        command.upgrade(cfg, "head")


def test_0007_constraints_and_guarded_downgrade(db_url: str, engine) -> None:  # noqa: ANN001
    cfg = alembic_config(db_url)
    command.upgrade(cfg, "head")
    probe = create_engine(db_url)
    try:
        insp = inspect(probe)
        uniques = {u["name"] for u in insp.get_unique_constraints("persona_runs")}
        assert "uq_persona_runs_eval_persona_repeat" in uniques
        fks = {f["name"]: f for f in insp.get_foreign_keys("persona_runs")}
        assert fks["fk_persona_runs_evaluation"]["constrained_columns"] == [
            "evaluation_id", "panel_id", "site_version_id",
        ]  # fmt: skip

        with probe.begin() as conn:
            ids = _seed_m5(conn, with_run=False)
            conn.execute(
                text(
                    "INSERT INTO persona_evaluations (id, business_id, panel_id, site_version_id) "
                    "VALUES (:id, :b, :p, :sv)"
                ),
                {"id": ids["evaluation"], "b": ids["business"], "p": ids["panel"], "sv": ids["sv"]},
            )

        # Refuses and keeps the data.
        with pytest.raises(RuntimeError, match="would destroy M5/M6 data"):
            command.downgrade(cfg, "0006")
        with probe.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM persona_evaluations")).scalar_one() == 1

        # Explicitly authorised destructive downgrade removes only M5/M6 rows.
        cfg.cmd_opts = Namespace(x=["allow_data_loss=true"])
        command.downgrade(cfg, "0006")
        cfg.cmd_opts = None
        with probe.connect() as conn:
            assert conn.execute(text("SELECT count(*) FROM persona_panels")).scalar_one() == 0
            assert conn.execute(text("SELECT count(*) FROM businesses")).scalar_one() == 1
            assert conn.execute(text("SELECT count(*) FROM site_versions")).scalar_one() == 1
        command.upgrade(cfg, "head")
        with probe.begin() as conn:
            _wipe(conn)
    finally:
        probe.dispose()
        cfg.cmd_opts = None
        command.upgrade(cfg, "head")
