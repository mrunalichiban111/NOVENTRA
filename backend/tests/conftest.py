"""Shared fixtures. Tests use a real Postgres database named *_test (never the app DB)."""

import os
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

os.environ.setdefault("MOCK_LLM", "1")

BACKEND_DIR = Path(__file__).resolve().parent.parent


def _test_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        pytest.skip("TEST_DATABASE_URL is not set", allow_module_level=True)
    name = make_url(url).database or ""
    if not name.endswith("_test"):
        pytest.exit(f"Refusing to run tests against database {name!r}: name must end in _test")
    return url


def alembic_config(url: str) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    return cfg


@pytest.fixture(scope="session")
def db_url() -> str:
    return _test_url()


@pytest.fixture(scope="session")
def engine(db_url: str) -> Iterator[Engine]:
    cfg = alembic_config(db_url)
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    eng = create_engine(db_url, pool_pre_ping=True)
    yield eng
    eng.dispose()


@pytest.fixture(autouse=True)
def _clean_db(request: pytest.FixtureRequest) -> Iterator[None]:
    """Empty every table after each DB-using test."""
    yield
    if "engine" not in request.fixturenames and "session" not in request.fixturenames:
        return
    eng: Engine = request.getfixturevalue("engine")
    with eng.begin() as conn:
        names = conn.execute(
            text(
                "SELECT string_agg(quote_ident(tablename), ', ') FROM pg_tables "
                "WHERE schemaname = 'public' AND tablename <> 'alembic_version'"
            )
        ).scalar()
        if names:
            conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
def session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


@pytest.fixture
def session(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    with session_factory() as s:
        yield s


@pytest.fixture
def client(session_factory: sessionmaker[Session]) -> Iterator[TestClient]:

    from app.api.ratelimit import limiter
    from app.database import get_session
    from app.main import app

    def override() -> Iterator[Session]:
        with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = override
    limiter.reset()
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def network_attempts(monkeypatch: pytest.MonkeyPatch) -> Iterator[list[str]]:
    """No test may reach the real network (so none can call the Anthropic API).

    Real traffic (httpx and the SDK's httpx2) goes through HTTPTransport; it is replaced by a stub that records the
    host and raises. Any recorded attempt fails the test at teardown. A test that deliberately
    provokes an attempt must `.clear()` the list itself. (Starlette's TestClient and psycopg do
    not use this transport, so M1 tests are unaffected.)
    """
    attempts: list[str] = []

    def blocked(self: object, request: object) -> object:
        attempts.append(request.url.host)  # type: ignore[attr-defined]
        raise RuntimeError("real network access is blocked in tests")

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", blocked)
    try:  # the Anthropic SDK 2.x uses its own `httpx2`; block it as well
        import httpx2

        monkeypatch.setattr(httpx2.HTTPTransport, "handle_request", blocked)
    except ImportError:
        pass
    yield attempts
    assert not attempts, f"test attempted real network access to: {attempts}"
