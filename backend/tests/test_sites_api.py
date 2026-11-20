"""M4B site routes and the internal Caddy TLS permission check. *_test DBs only; mock LLM;
mock provider; no network."""

import uuid

import pytest
from sqlalchemy import update

from app import config
from app.api import ratelimit
from app.deploy.providers import MockDeployProvider
from app.deploy.site_model import SiteLinks
from app.models import Business
from app.pipeline import repository
from app.pipeline.handlers import pipeline_stages
from app.pipeline.runner import run_business
from app.pipeline.status import RETRYABLE_STAGES
from tests.m3_helpers import SpyLLM
from tests.test_deploy_pipeline import BASE, HOST, pending_business

TOKEN = "t0k3n-for-tests-only-0123456789abcdef"


@pytest.fixture(autouse=True)
def _config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "BASE_DOMAIN", BASE)
    monkeypatch.setattr(config, "DEPLOY_PROVIDER", "mock")
    monkeypatch.setattr(config, "APP_ENV", "development")
    monkeypatch.setattr(config, "SITE_REQUIRE_PUBLIC", False)
    monkeypatch.setattr(config, "SITE_DNS_MODE", "skip")
    monkeypatch.setattr(config, "SITE_HTTP_CHECK", False)
    monkeypatch.setattr(config, "WAITLIST_POST_URL", "")
    monkeypatch.setattr(config, "ABUSE_REPORT_EMAIL", "")
    monkeypatch.setattr(config, "INTERNAL_TLS_ASK_TOKEN", TOKEN)


def deploy_worker(sf, bid) -> None:  # noqa: ANN001
    provider = MockDeployProvider()
    stages = pipeline_stages(SpyLLM(), provider_factory=lambda: provider, links=SiteLinks())
    with sf() as s:
        repository.claim_next(s, "w1", 60, exclude=RETRYABLE_STAGES - stages.keys())
    run_business(sf, bid, worker_id="w1", stages=stages)


def set_status(sf, bid, value: str) -> None:  # noqa: ANN001
    with sf() as s:
        s.execute(update(Business).where(Business.id == bid).values(status=value))
        s.commit()


# --- POST /deploy ------------------------------------------------------------------------------


def test_deploy_request_is_accepted_then_idempotent(client, session_factory) -> None:
    bid = pending_business(session_factory)
    r = client.post(f"/api/businesses/{bid}/deploy")
    assert r.status_code == 202
    assert r.json() == {
        "business_id": str(bid),
        "status": "DEPLOYING",
        "host": HOST,
        "changed": True,
    }
    again = client.post(f"/api/businesses/{bid}/deploy")
    assert again.status_code == 200 and again.json()["changed"] is False


@pytest.mark.parametrize(
    ("patch", "code"),
    [
        ({"DEPLOY_PROVIDER": ""}, "deploy_not_configured"),
        ({"BASE_DOMAIN": ""}, "deploy_not_configured"),
        ({"APP_ENV": "production"}, "deploy_misconfigured"),  # mock provider in production
        ({"SITE_HTTP_CHECK": True}, "deploy_misconfigured"),  # HTTPS check with DNS skipped
    ],
)
def test_deploy_refused_with_503_when_not_safely_configured(
    client, session_factory, monkeypatch: pytest.MonkeyPatch, patch: dict, code: str
) -> None:
    bid = pending_business(session_factory)
    for name, value in patch.items():
        monkeypatch.setattr(config, name, value)
    r = client.post(f"/api/businesses/{bid}/deploy")
    assert r.status_code == 503 and r.json()["detail"]["code"] == code
    assert "mock" not in r.json()["detail"]["message"]  # no config details leaked
    assert client.get(f"/api/businesses/{bid}").json()["status"] == "DOMAIN_PENDING"


def test_deploy_errors(client, session_factory) -> None:
    assert client.post(f"/api/businesses/{uuid.uuid4()}/deploy").status_code == 404
    bid = pending_business(session_factory)
    set_status(session_factory, bid, "GATED")
    r = client.post(f"/api/businesses/{bid}/deploy")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "not_eligible"
    with session_factory() as s:
        other = Business(idea_text="x", status="DOMAIN_PENDING", domain="other.example.in")
        s.add(other)
        s.commit()
        oid = other.id
    r = client.post(f"/api/businesses/{oid}/deploy")
    assert r.status_code == 409 and r.json()["detail"]["code"] == "no_claim"
    assert client.post("/api/businesses/not-a-uuid/deploy").status_code == 422


def test_deploy_is_rate_limited(client, session_factory) -> None:
    bid = pending_business(session_factory)
    ratelimit.limiter.reset()
    ratelimit.limiter.limit = 2
    try:
        codes = [client.post(f"/api/businesses/{bid}/deploy").status_code for _ in range(3)]
    finally:
        ratelimit.limiter.limit = ratelimit.RATE_LIMIT_PER_MINUTE
    assert codes == [202, 200, 429]


# --- GET /site and /deployments ------------------------------------------------------------------


def test_site_and_deployment_history_after_live(client, session_factory) -> None:
    bid = pending_business(session_factory)
    before = client.get(f"/api/businesses/{bid}/site").json()
    assert (before["live_version"], before["url"], before["versions"]) == (None, None, [])

    client.post(f"/api/businesses/{bid}/deploy")
    deploy_worker(session_factory, bid)

    site = client.get(f"/api/businesses/{bid}/site").json()
    assert site["status"] == "LIVE" and site["live_version"] == 1
    assert site["url"] == f"https://{HOST}/"
    assert (site["verification_level"], site["publicly_reachable"]) == ("simulated", False)
    (v1,) = site["versions"]
    assert v1["status"] == "deployed" and len(v1["content_hash"]) == 64
    assert "content_json" not in v1 and "manifest_json" not in v1  # no artifacts in the API

    deps = client.get(f"/api/businesses/{bid}/deployments").json()["deployments"]
    assert [(d["version"], d["attempt"], d["status"]) for d in deps] == [(1, 1, "verified")]
    assert deps[0]["publicly_reachable"] is False and deps[0]["checks"][0]["simulated"] is True


def test_site_routes_404_for_unknown_business(client) -> None:
    for path in ("site", "deployments"):
        assert client.get(f"/api/businesses/{uuid.uuid4()}/{path}").status_code == 404


# --- internal TLS permission check ---------------------------------------------------------------

ASK = "/internal/sites/tls-allowed"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def live_host(session_factory, status_value: str = "LIVE") -> str:  # noqa: ANN001
    bid = pending_business(session_factory)
    set_status(session_factory, bid, status_value)
    return HOST


@pytest.mark.parametrize("status_value", ["DEPLOYING", "LIVE"])
def test_tls_allowed_for_a_claimed_deploying_or_live_host(
    client, session_factory, status_value: str
) -> None:
    live_host(session_factory, status_value)
    r = client.get(ASK, params={"domain": HOST}, headers=AUTH)
    assert r.status_code == 200 and r.content == b""


@pytest.mark.parametrize("status_value", ["DOMAIN_PENDING", "GATED", "FAILED", "REJECTED", "DONE"])
def test_tls_refused_for_other_statuses(client, session_factory, status_value: str) -> None:
    live_host(session_factory, status_value)
    assert client.get(ASK, params={"domain": HOST}, headers=AUTH).status_code == 403


@pytest.mark.parametrize(
    "domain",
    [
        "other.example.in",  # not claimed
        "*.example.in",
        "TIFFO.example.in",
        "tiffo.example.in.",
        "tiffo.example.in:443",
        "x.tiffo.example.in",
        "tiffo.evil.in",
        "example.in",
        "",
        "tiffo.example.in/../x",
    ],
)
def test_tls_refused_for_unclaimed_or_malformed_hosts(client, session_factory, domain: str) -> None:
    live_host(session_factory)
    r = client.get(ASK, params={"domain": domain}, headers=AUTH)
    assert r.status_code == 403 and r.content == b""


def test_tls_needs_exactly_one_domain(client, session_factory) -> None:
    live_host(session_factory)
    assert client.get(ASK, headers=AUTH).status_code == 400
    r = client.get(f"{ASK}?domain={HOST}&domain=other.example.in", headers=AUTH)
    assert r.status_code == 400


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"Authorization": "Bearer wrong-token"},
        {"Authorization": f"Basic {TOKEN}"},
        {"Authorization": TOKEN},
        {"Authorization": f"Bearer {TOKEN}x"},
        {"Authorization": "Bearer "},
    ],
)
def test_tls_requires_the_bearer_token(client, session_factory, headers: dict) -> None:
    live_host(session_factory)
    assert client.get(ASK, params={"domain": HOST}, headers=headers).status_code == 401


def test_token_in_the_query_string_is_not_accepted(client, session_factory) -> None:
    live_host(session_factory)
    r = client.get(ASK, params={"domain": HOST, "token": TOKEN, "access_token": TOKEN})
    assert r.status_code == 401


@pytest.mark.parametrize(
    "header",
    ["X-Forwarded-For", "X-Forwarded-Host", "Forwarded", "X-Real-IP"],
)
def test_tls_refuses_proxied_requests_even_with_the_token(
    client, session_factory, header: str
) -> None:
    live_host(session_factory)
    headers = AUTH | {header: "203.0.113.9"}
    assert client.get(ASK, params={"domain": HOST}, headers=headers).status_code == 403


def test_tls_endpoint_disabled_without_a_token(
    client, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    live_host(session_factory)
    monkeypatch.setattr(config, "INTERNAL_TLS_ASK_TOKEN", "")
    assert client.get(ASK, params={"domain": HOST}, headers=AUTH).status_code == 404
    assert client.get(ASK, params={"domain": HOST}).status_code == 404


def test_token_comparison_is_constant_time(
    client, session_factory, monkeypatch: pytest.MonkeyPatch
) -> None:
    import app.api.sites as sites

    calls: list[tuple[bytes, bytes]] = []
    real = sites.hmac.compare_digest

    def spy(a: bytes, b: bytes) -> bool:
        calls.append((a, b))
        return real(a, b)

    monkeypatch.setattr(sites.hmac, "compare_digest", spy)
    live_host(session_factory)
    client.get(ASK, params={"domain": HOST}, headers=AUTH)
    assert calls == [(TOKEN.encode(), TOKEN.encode())]


def test_internal_route_is_not_under_api_and_not_in_openapi(client) -> None:
    assert client.get(f"/api{ASK}", params={"domain": HOST}, headers=AUTH).status_code == 404
    assert "/internal/sites/tls-allowed" not in client.get("/openapi.json").json()["paths"]
