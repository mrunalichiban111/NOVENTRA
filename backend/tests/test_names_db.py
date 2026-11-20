"""DB tests for name candidates and subdomain claims (M4A). Run only against *_test databases.

Claim uniqueness is enforced by partial unique indexes on selected rows; these tests exercise
the indexes directly. The selection service and its API arrive in M4B.
"""

import threading
import uuid
from decimal import Decimal

import pytest
from alembic import command
from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.models import Business, DomainCandidate, DomainCheck
from app.names import config as c
from app.names.normalize import normalize_candidates
from app.names.rank import build_candidates
from app.names.repository import list_candidates, replace_unselected
from tests.conftest import alembic_config


def make_business(session: Session) -> Business:
    b = Business(idea_text="GST filing help for freelancers.", status="GATED")
    session.add(b)
    session.flush()
    return b


def records(*names: str):  # noqa: ANN201
    return build_candidates(
        normalize_candidates((n, "why") for n in names), keywords=frozenset(), apex=None
    )


def add_candidate(session: Session, business: Business, slug: str, **kw) -> DomainCandidate:  # noqa: ANN003
    row = DomainCandidate(business_id=business.id, name=slug.title(), slug=slug, valid=True, **kw)
    session.add(row)
    session.flush()
    return row


def test_replace_unselected_persists_and_keeps_the_claim(session: Session) -> None:
    b = make_business(session)
    first = replace_unselected(session, b.id, "names-1", records("Taxpal", "Sumwise", "Api"))
    first[0].selected = True
    session.commit()

    replace_unselected(session, b.id, "names-2", records("Filewise", "Taxpal", "--x"))
    session.commit()
    rows = list_candidates(session, b.id)
    assert rows[0].selected and rows[0].run_id == "names-1"
    assert {(r.name, r.run_id) for r in rows[1:]} == {
        ("Filewise", "names-2"), ("Taxpal", "names-2"), ("--x", "names-2"),
    }  # fmt: skip
    bad = next(r for r in rows if r.name == "--x")
    assert (bad.valid, bad.invalid_reason, bad.rank, bad.score_total) == (
        False, c.EDGE_HYPHEN, None, None,
    )  # fmt: skip
    good = next(r for r in rows if r.name == "Filewise")
    assert good.valid and good.rank is not None and good.score_factors["length"]["weight"] == 25
    assert isinstance(good.score_total, Decimal)


def test_unselected_slugs_are_not_globally_unique(session: Session) -> None:
    a, b = make_business(session), make_business(session)
    add_candidate(session, a, "taxpal")
    add_candidate(session, b, "taxpal")
    session.commit()


def test_claimed_slug_is_unique_across_businesses(session: Session) -> None:
    a, b = make_business(session), make_business(session)
    add_candidate(session, a, "taxpal", selected=True)
    session.commit()
    with pytest.raises(IntegrityError, match="uq_domain_candidates_claimed_slug"):
        add_candidate(session, b, "taxpal", selected=True)


def test_one_selected_candidate_per_business(session: Session) -> None:
    b = make_business(session)
    add_candidate(session, b, "taxpal", selected=True)
    session.commit()
    with pytest.raises(IntegrityError, match="uq_domain_candidates_one_selected"):
        add_candidate(session, b, "sumwise", selected=True)


def test_domain_checks_status_and_cascade(session: Session) -> None:
    b = make_business(session)
    cand = add_candidate(session, b, "taxpal")
    session.add(DomainCheck(candidate_id=cand.id, tld=".in", status="AVAILABLE", raw_price=1))
    session.commit()
    session.add(DomainCheck(candidate_id=cand.id, tld=".com", status="MAYBE"))
    with pytest.raises(IntegrityError, match="ck_domain_checks_status"):
        session.flush()
    session.rollback()
    session.delete(session.get(DomainCandidate, cand.id))
    session.commit()
    assert session.scalar(select(DomainCheck)) is None


def test_concurrent_claims_of_the_same_slug_exactly_one_wins(
    session_factory: sessionmaker[Session],
) -> None:
    ids = []
    with session_factory() as s:
        for _ in range(2):
            ids.append(add_candidate(s, make_business(s), "taxpal").id)
        s.commit()

    barrier = threading.Barrier(2)
    outcomes: list[str] = []
    lock = threading.Lock()

    def claim(candidate_id: uuid.UUID) -> None:
        with session_factory() as s:
            barrier.wait()
            try:
                s.execute(
                    update(DomainCandidate)
                    .where(DomainCandidate.id == candidate_id)
                    .values(selected=True)
                )
                s.commit()
                result = "ok"
            except IntegrityError:
                s.rollback()
                result = "conflict"
            with lock:
                outcomes.append(result)

    threads = [threading.Thread(target=claim, args=(i,)) for i in ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
    assert sorted(outcomes) == ["conflict", "ok"]
    with session_factory() as s:
        assert s.scalar(text("SELECT count(*) FROM domain_candidates WHERE selected")) == 1


# --- migration 0004 round trip with data --------------------------------------------------


def _rows(conn) -> set[tuple]:  # noqa: ANN001
    return set(
        conn.execute(text("SELECT name, tld, available, price, score FROM domain_candidates")).all()
    )


def test_migration_0004_round_trip_preserves_data(db_url: str, engine) -> None:  # noqa: ANN001
    cfg = alembic_config(db_url)
    command.downgrade(cfg, "0003")
    try:
        biz = uuid.uuid4()
        with engine.begin() as conn:
            conn.execute(
                text("INSERT INTO businesses (id, idea_text) VALUES (:id, 'Tea subscription')"),
                {"id": biz},
            )
            conn.execute(
                text(
                    "INSERT INTO domain_candidates (id, business_id, name, tld, available, price) "
                    "VALUES (gen_random_uuid(), :b, 'tea', '.in', true, 499.00), "
                    "(gen_random_uuid(), :b, 'tea', '.com', false, NULL)"
                ),
                {"b": biz},
            )
        legacy = {("tea", ".in", True, Decimal("499.00"), None), ("tea", ".com", False, None, None)}

        command.upgrade(cfg, "0004")
        with engine.begin() as conn:
            assert _rows(conn) == legacy
            marks = conn.execute(
                text("SELECT DISTINCT slug, valid, invalid_reason, selected FROM domain_candidates")
            ).all()
            assert marks == [("tea", False, c.LEGACY_UNVALIDATED, False)]

            def cand(name: str, rank: int, total: str, selected: bool = False) -> uuid.UUID:
                cid = uuid.uuid4()
                conn.execute(
                    text(
                        "INSERT INTO domain_candidates (id, business_id, name, slug, valid, rank, "
                        "score_total, selected) VALUES (:id, :b, :n, :slug, true, :r, :t, :s)"
                    ),
                    {
                        "id": cid,
                        "b": biz,
                        "n": name,
                        "slug": name.lower(),
                        "r": rank,
                        "t": total,
                        "s": selected,
                    },
                )
                return cid

            def check(cid: uuid.UUID, tld: str, status: str, price: str | None = None) -> None:
                conn.execute(
                    text(
                        "INSERT INTO domain_checks (id, candidate_id, tld, status, raw_price) "
                        "VALUES (gen_random_uuid(), :c, :t, :s, :p)"
                    ),
                    {"c": cid, "t": tld, "s": status, "p": price},
                )

            a = cand("Taxpal", 2, "80.00", selected=True)  # selected beats a better rank
            check(a, ".in", "AVAILABLE", "1299.0000")
            check(a, ".com", "UNKNOWN")
            b = cand("Taxpal", 1, "81.00")  # same (name, tld) key: loses to the selected one
            check(b, ".in", "UNAVAILABLE", "5.0000")
            check(b, ".co", "UNAVAILABLE")  # unique key: kept
            cand("Sumwise", 3, "70.00")  # no checks: one row with tld ''
            cand("Sumwise", 5, "60.00")  # duplicate without checks: dropped
            e = cand("tea", 4, "50.00")  # collides with a legacy row: legacy wins
            check(e, ".in", "UNAVAILABLE", "1.0000")
            check(e, ".co", "AVAILABLE", "99999999999.0000")  # too big for numeric(12,2)

        command.downgrade(cfg, "0003")
        with engine.begin() as conn:
            assert _rows(conn) == legacy | {
                ("Taxpal", ".in", True, Decimal("1299.00"), Decimal("80.00")),
                ("Taxpal", ".com", None, None, Decimal("80.00")),
                ("Taxpal", ".co", False, None, Decimal("81.00")),
                ("Sumwise", "", None, None, Decimal("70.00")),
                ("tea", ".co", True, None, Decimal("50.00")),
            }
            assert "domain_checks" not in {
                r[0] for r in conn.execute(text("SELECT tablename FROM pg_tables")).all()
            }
    finally:
        command.upgrade(cfg, "head")

    with engine.begin() as conn:
        assert (
            conn.execute(
                text("SELECT count(*) FROM domain_candidates WHERE invalid_reason <> :r"),
                {"r": c.LEGACY_UNVALIDATED},
            ).scalar()
            == 0
        )
