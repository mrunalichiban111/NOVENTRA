import uuid

import pytest
from sqlalchemy import inspect, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models import (
    Approval,
    Assumption,
    AuditLog,
    Base,
    Business,
    DomainCandidate,
    ImprovementAttempt,
    PersonaPanel,
    PersonaRun,
    Scorecard,
    SiteVersion,
    WaitlistSignup,
)

EXPECTED_TABLES = {
    "businesses", "assumptions", "scorecards", "domain_candidates", "site_versions",
    "persona_panels", "persona_runs", "improvement_attempts", "approvals", "audit_log",
    "waitlist_signups",
}  # fmt: skip


def make_business(session: Session) -> Business:
    b = Business(idea_text="A tea subscription. It ships monthly.")
    session.add(b)
    session.flush()
    return b


def test_all_tables_are_modelled() -> None:
    assert EXPECTED_TABLES <= set(Base.metadata.tables)


def test_tables_exist_in_database(engine) -> None:  # noqa: ANN001
    assert EXPECTED_TABLES <= set(inspect(engine).get_table_names())


def test_business_defaults(session: Session) -> None:
    b = make_business(session)
    session.commit()
    session.refresh(b)
    assert isinstance(b.id, uuid.UUID)
    assert (b.market, b.status, b.attempts) == ("India", "INTAKE", 0)
    assert b.created_at is not None and b.updated_at is not None


def test_every_entity_can_be_created(session: Session) -> None:
    b = make_business(session)
    panel = PersonaPanel(business_id=b.id, personas_json=[{"id": "p1"}])
    site = SiteVersion(business_id=b.id, version=1, content_json={"h": "x"}, content_hash="ab")
    session.add_all([panel, site])
    session.flush()
    session.add_all(
        [
            Assumption(business_id=b.id, key="cac", low=1, base=2, high=3, source="llm"),
            Scorecard(business_id=b.id, version=1, stage="pre", verdict="go", details_json={"a": 1}),
            DomainCandidate(business_id=b.id, name="tea", tld=".in", available=True),
            PersonaRun(
                panel_id=panel.id, site_version_id=site.id, persona_id="p1", run_idx=0,
                decision="convert", confidence=0.8,
            ),
            ImprovementAttempt(business_id=b.id, from_version=1, to_version=2, outcome="accepted"),
            Approval(business_id=b.id, action_type="takedown", payload_json={"x": 1}),
            AuditLog(business_id=b.id, actor="user", action="x"),
            WaitlistSignup(business_id=b.id, email="a@example.com"),
        ]
    )  # fmt: skip
    session.commit()
    assert session.scalar(select(Scorecard.details_json)) == {"a": 1}


@pytest.mark.parametrize(
    "factory",
    [
        lambda b: Assumption(business_id=b, key="k", source="bogus"),
        lambda b: Scorecard(business_id=b, version=1, stage="mid", verdict="go"),
        lambda b: Scorecard(business_id=b, version=1, stage="pre", verdict="maybe"),
        lambda b: Approval(business_id=b, action_type="x", status="weird"),
        lambda b: ImprovementAttempt(business_id=b, from_version=1, to_version=2, outcome="?"),
    ],
)
def test_check_constraints(session: Session, factory) -> None:  # noqa: ANN001
    b = make_business(session)
    session.add(factory(b.id))
    with pytest.raises(IntegrityError):
        session.flush()


def test_invalid_business_status_rejected(session: Session) -> None:
    session.add(Business(idea_text="x", status="NOPE"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_unique_constraints(session: Session) -> None:
    b = make_business(session)
    session.add_all(
        [
            WaitlistSignup(business_id=b.id, email="a@example.com"),
            WaitlistSignup(business_id=b.id, email="a@example.com"),
        ]
    )
    with pytest.raises(IntegrityError):
        session.flush()


def test_site_version_unique_per_business(session: Session) -> None:
    b = make_business(session)
    for _ in range(2):
        session.add(SiteVersion(business_id=b.id, version=1, content_json={}, content_hash="h"))
    with pytest.raises(IntegrityError):
        session.flush()


def test_children_cascade_but_audit_is_restricted(session: Session) -> None:
    b = make_business(session)
    session.add(Assumption(business_id=b.id, key="k", source="user"))
    session.flush()
    session.delete(b)
    session.commit()  # no audit rows: cascade removes the assumption
    assert session.scalar(select(Assumption)) is None

    b2 = make_business(session)
    session.add(AuditLog(business_id=b2.id, actor="user", action="x"))
    session.commit()
    session.delete(b2)
    with pytest.raises(IntegrityError):
        session.commit()
