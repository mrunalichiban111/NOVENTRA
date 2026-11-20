"""DB tests for M4B site versions and deployment history (migration 0005). *_test DBs only.

These exercise the constraints directly: content may be missing only while generating,
at most one unfinished version per business, unique versions, and at most one in-flight
deployment per business. The services that use them arrive in later M4B blocks.
"""

import uuid

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import Business, Deployment, SiteVersion
from tests.conftest import alembic_config

CONTENT = {"hero": {"headline": "x"}}


def make_business(session: Session, domain: str = "tiffo.x.in") -> Business:
    b = Business(idea_text="Tiffin service for students.", status="DEPLOYING", domain=domain)
    session.add(b)
    session.flush()
    return b


def version(business: Business, n: int, status: str, **kw) -> SiteVersion:  # noqa: ANN003
    if status not in ("generating", "abandoned"):
        kw.setdefault("content_json", CONTENT)
        kw.setdefault("content_hash", "c" * 64)
    return SiteVersion(business_id=business.id, version=n, status=status, **kw)


def test_generating_row_needs_no_content_and_has_defaults(session: Session) -> None:
    b = make_business(session)
    row = version(b, 1, "generating", generation_run_id="run-1", generation_attempt=1)
    session.add(row)
    session.commit()
    session.refresh(row)
    assert row.content_json is None and row.manifest_json == {}
    assert row.generation_attempt == 1 and row.updated_at is not None


@pytest.mark.parametrize("status", ["draft", "built", "failed", "deployed"])
def test_content_required_outside_generation(session: Session, status: str) -> None:
    b = make_business(session)
    session.add(SiteVersion(business_id=b.id, version=1, status=status))
    with pytest.raises(IntegrityError, match="ck_site_versions_content"):
        session.flush()


def test_unknown_status_and_negative_attempt_rejected(session: Session) -> None:
    b = make_business(session)
    session.add(version(b, 1, "publishing"))
    with pytest.raises(IntegrityError, match="ck_site_versions_status"):
        session.flush()
    session.rollback()
    b = make_business(session)
    session.add(version(b, 1, "draft", generation_attempt=-1))
    with pytest.raises(IntegrityError, match="ck_site_versions_generation_attempt"):
        session.flush()


@pytest.mark.parametrize("first", ["generating", "abandoned", "draft", "built", "failed"])
def test_only_one_open_version_per_business(session: Session, first: str) -> None:
    b = make_business(session)
    session.add(version(b, 1, first))
    session.flush()
    session.add(version(b, 2, "generating"))
    with pytest.raises(IntegrityError, match="uq_site_versions_one_open"):
        session.flush()


def test_closed_versions_do_not_block_a_new_open_one(session: Session) -> None:
    b = make_business(session)
    other = make_business(session, "other.x.in")
    session.add_all(
        [
            version(b, 1, "superseded"),
            version(b, 2, "deployed"),
            version(b, 3, "generating"),
            version(other, 1, "generating"),  # a different business is independent
        ]
    )
    session.commit()


def test_version_numbers_are_unique_per_business(session: Session) -> None:
    b = make_business(session)
    session.add_all([version(b, 1, "deployed"), version(b, 1, "superseded")])
    with pytest.raises(IntegrityError, match="uq_site_versions_business_version"):
        session.flush()


def deployment(b: Business, site: SiteVersion, attempt: int, status: str, **kw) -> Deployment:  # noqa: ANN003
    return Deployment(
        business_id=b.id,
        site_version_id=site.id,
        host=b.domain,
        provider="mock",
        attempt=attempt,
        status=status,
        **kw,
    )


def test_deployment_history_and_single_in_flight(session: Session) -> None:
    b = make_business(session)
    site = version(b, 1, "built")
    session.add(site)
    session.flush()
    session.add_all(
        [
            deployment(b, site, 1, "failed", error="health check failed"),
            deployment(b, site, 2, "verified", verification_level="simulated"),
            deployment(b, site, 3, "started"),
        ]
    )
    session.commit()
    session.add(deployment(b, site, 4, "activated"))
    with pytest.raises(IntegrityError, match="uq_deployments_one_in_flight"):
        session.flush()


def test_deployment_attempt_unique_per_version(session: Session) -> None:
    b = make_business(session)
    site = version(b, 1, "built")
    session.add(site)
    session.flush()
    session.add_all([deployment(b, site, 1, "failed"), deployment(b, site, 1, "failed")])
    with pytest.raises(IntegrityError, match="uq_deployments_version_attempt"):
        session.flush()


@pytest.mark.parametrize(
    ("kw", "constraint"),
    [
        ({"status": "verified"}, "ck_deployments_verified_level"),
        ({"status": "verified", "verification_level": "dns"}, "ck_deployments_verification_level"),
        ({"status": "live"}, "ck_deployments_status"),
        ({"status": "failed", "attempt": 0}, "ck_deployments_attempt"),
    ],
)
def test_deployment_checks(session: Session, kw: dict, constraint: str) -> None:
    b = make_business(session)
    site = version(b, 1, "built")
    session.add(site)
    session.flush()
    attempt = kw.pop("attempt", 1)
    session.add(deployment(b, site, attempt, **kw))
    with pytest.raises(IntegrityError, match=constraint):
        session.flush()


# --- migration 0005 round trip with data --------------------------------------------------


def test_migration_0005_round_trip(db_url: str, engine) -> None:  # noqa: ANN001
    cfg = alembic_config(db_url)
    command.downgrade(cfg, "0004")
    try:
        legacy_biz = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO businesses (id, idea_text) VALUES (:id, 'Legacy site')"),
                {"id": legacy_biz},
            )
            conn.execute(
                text(
                    "INSERT INTO site_versions (id, business_id, version, content_json, "
                    "content_hash, status) VALUES (gen_random_uuid(), :b, 1, '{\"a\": 1}', "
                    "'h1', 'deployed'), (gen_random_uuid(), :b, 2, '{\"a\": 2}', 'h2', 'draft')"
                ),
                {"b": legacy_biz},
            )

        command.upgrade(cfg, "0005")
        built_biz, gen_biz = uuid.uuid4(), uuid.uuid4()
        with engine.begin() as conn:
            # pre-M4B rows survive unchanged, with defaults for the new columns
            rows = conn.execute(
                text(
                    "SELECT version, status, content_hash, manifest_json, generation_attempt "
                    "FROM site_versions ORDER BY version"
                )
            ).all()
            assert rows == [(1, "deployed", "h1", {}, 0), (2, "draft", "h2", {}, 0)]

            for biz in (built_biz, gen_biz):
                conn.execute(
                    text("INSERT INTO businesses (id, idea_text) VALUES (:id, 'M4B site')"),
                    {"id": biz},
                )
            site_id = uuid.uuid4()
            conn.execute(
                text(
                    "INSERT INTO site_versions (id, business_id, version, content_json, "
                    "content_hash, status, artifact_hash) VALUES "
                    "(:id, :b, 1, '{\"a\": 3}', 'h3', 'built', 'art')"
                ),
                {"id": site_id, "b": built_biz},
            )
            conn.execute(
                text(
                    "INSERT INTO deployments (id, business_id, site_version_id, host, provider, "
                    "attempt, status) VALUES (gen_random_uuid(), :b, :s, 'a.x.in', 'mock', 1, "
                    "'failed')"
                ),
                {"b": built_biz, "s": site_id},
            )
            conn.execute(
                text(
                    "INSERT INTO site_versions (id, business_id, version, status, "
                    "generation_run_id) VALUES (gen_random_uuid(), :b, 1, 'generating', 'r1')"
                ),
                {"b": gen_biz},
            )

        command.downgrade(cfg, "0004")
        with engine.begin() as conn:
            rows = conn.execute(
                text(
                    "SELECT business_id, version, status, content_hash FROM site_versions "
                    "ORDER BY content_hash"
                )
            ).all()
            # content-less generating row deleted; built -> draft; legacy rows untouched
            assert rows == [
                (legacy_biz, 1, "deployed", "h1"),
                (legacy_biz, 2, "draft", "h2"),
                (built_biz, 1, "draft", "h3"),
            ]
            tables = {r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables")).all()}
            assert "deployments" not in tables
            nullable = conn.execute(
                text(
                    "SELECT is_nullable FROM information_schema.columns "
                    "WHERE table_name = 'site_versions' AND column_name = 'content_json'"
                )
            ).scalar_one()
            assert nullable == "NO"
    finally:
        command.upgrade(cfg, "head")
