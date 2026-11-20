"""Deployment providers and configuration guards (FR-SITE-3/4/5, M4B R1). tmp_path stores,
fake resolvers and httpx.MockTransport only: no VPS, no DNS writes, no network."""

import dataclasses
from pathlib import Path

import httpx
import pytest

from app import config
from app.deploy.dns import MockDns, PreconfiguredDns, SkipDns
from app.deploy.health import HttpChecker, PublicVerifier, acceptable_for_live
from app.deploy.providers import (
    DeployConfigError,
    DeployNotConfigured,
    DeploySettings,
    LocalReleaseProvider,
    MockDeployProvider,
    config_problems,
    get_provider,
)
from app.deploy.storage import ReleaseConflict, ReleaseStore, StorageError
from app.models import VerificationLevel
from tests.deploy.helpers import BASE, HOST, rendered


@pytest.fixture
def root(tmp_path: Path) -> Path:
    r = tmp_path / "sites"
    r.mkdir()
    r.chmod(0o755)
    return r


def serving(root: Path, status: int = 200):  # noqa: ANN201
    """A MockTransport that serves <root>/<host>/current/index.html like Caddy would."""

    def handler(req: httpx.Request) -> httpx.Response:
        page = root / req.url.host / "current" / "index.html"
        if status != 200 or not page.exists():
            return httpx.Response(status if status != 200 else 404)
        return httpx.Response(200, content=page.read_bytes())

    return httpx.MockTransport(handler)


def http(transport: httpx.MockTransport) -> HttpChecker:
    return HttpChecker(timeout_s=0, transport=transport, sleep=lambda s: None)


def real_dns(addrs: list[str] | None = None) -> PreconfiguredDns:
    return PreconfiguredDns(
        timeout_s=0,
        resolver=lambda h: list(["203.0.113.7"] if addrs is None else addrs),
        sleep=lambda s: None,
    )


def pv(dns, http_checker) -> PublicVerifier:  # noqa: ANN001
    """Verifier with an already-expired shared deadline: exactly one attempt per check."""
    return PublicVerifier(dns, http_checker, timeout_s=0)


def local(root: Path, verifier: PublicVerifier | None = None) -> LocalReleaseProvider:
    return LocalReleaseProvider(ReleaseStore(root, BASE), verifier)


def deploy(provider, version: int = 1, headline: str | None = None):  # noqa: ANN001, ANN201
    site = rendered(headline)
    provider.publish(HOST, version, site)
    previous = provider.activate(HOST, version)
    return site, previous, provider.verify(HOST, version, site.manifest)


# --- LocalReleaseProvider verification levels ------------------------------------------------


def test_local_only_is_local_level_and_not_public(root: Path) -> None:
    _, _, report = deploy(local(root))
    assert (report.ok, report.level) == (True, VerificationLevel.LOCAL)
    assert report.to_json()["publicly_reachable"] is False
    assert acceptable_for_live(report, require_public=False)
    assert not acceptable_for_live(report, require_public=True)


def test_real_dns_plus_https_hash_match_is_public(root: Path) -> None:
    provider = local(root, pv(real_dns(), http(serving(root))))
    _, _, report = deploy(provider)
    assert (report.ok, report.level) == (True, VerificationLevel.PUBLIC)
    assert [c["name"] for c in report.checks] == ["active_release", "release_files", "dns", "https"]
    assert acceptable_for_live(report, require_public=True)


def test_skipped_dns_is_never_public(root: Path) -> None:
    provider = local(root, pv(SkipDns(), http(serving(root))))
    _, _, report = deploy(provider)
    assert (report.ok, report.level) == (True, VerificationLevel.LOCAL)
    assert not acceptable_for_live(report, require_public=True)


def test_mock_dns_caps_the_level_at_simulated(root: Path) -> None:
    provider = local(root, pv(MockDns(), http(serving(root))))
    _, _, report = deploy(provider)
    assert (report.ok, report.level) == (True, VerificationLevel.SIMULATED)


def test_dns_failure_fails_verification(root: Path) -> None:
    provider = local(root, pv(real_dns([]), http(serving(root))))
    _, _, report = deploy(provider)
    assert not report.ok and report.level is None and report.error.startswith("dns:")


def test_https_failure_fails_verification(root: Path) -> None:
    provider = local(root, pv(real_dns(), http(serving(root, status=502))))
    _, _, report = deploy(provider)
    assert not report.ok and "https: HTTP 502" in report.error


def test_served_page_differing_from_release_fails(root: Path) -> None:
    stale = httpx.MockTransport(lambda req: httpx.Response(200, content=b"old cached page"))
    provider = local(root, pv(real_dns(), http(stale)))
    _, _, report = deploy(provider)
    assert not report.ok and "does not match" in report.error


def test_tampered_file_on_disk_fails_before_any_remote_check(root: Path) -> None:
    calls: list = []
    transport = httpx.MockTransport(lambda req: calls.append(req) or httpx.Response(200))
    provider = local(root, pv(real_dns(), http(transport)))
    site = rendered()
    provider.publish(HOST, 1, site)
    provider.activate(HOST, 1)
    page = root / HOST / "releases" / "v1" / "index.html"
    page.chmod(0o644)
    page.write_bytes(b"defaced")
    report = provider.verify(HOST, 1, site.manifest)
    assert not report.ok and "hash mismatch" in report.error
    assert calls == []


def test_recorded_manifest_must_match_disk(root: Path) -> None:
    provider = local(root)
    site, _, _ = deploy(provider)
    other = rendered("Another headline")
    report = provider.verify(HOST, 1, other.manifest)
    assert not report.ok and "differs from the recorded manifest" in report.error


def test_verifying_an_inactive_version_fails(root: Path) -> None:
    provider = local(root)
    v1, _, _ = deploy(provider, 1)
    deploy(provider, 2, "Second version")
    report = provider.verify(HOST, 1, v1.manifest)
    assert not report.ok and "current is v2" in report.error


def test_failed_verification_restores_the_previous_release(root: Path) -> None:
    provider = local(root)
    deploy(provider, 1)
    site2 = rendered("Second version")
    provider.publish(HOST, 2, site2)
    previous = provider.activate(HOST, 2)
    page = root / HOST / "releases" / "v2" / "index.html"
    page.chmod(0o644)
    page.write_bytes(b"broken")
    assert not provider.verify(HOST, 2, site2.manifest).ok
    provider.activate(HOST, previous)  # what the deploy stage does on failure
    assert provider.active_version(HOST) == 1


def test_local_provider_publish_is_idempotent_and_immutable(root: Path) -> None:
    provider = local(root)
    site = rendered()
    assert provider.publish(HOST, 1, site).created
    assert not provider.publish(HOST, 1, site).created
    with pytest.raises(ReleaseConflict):
        provider.publish(HOST, 1, rendered("Changed"))


# --- MockDeployProvider ---------------------------------------------------------------------


def test_mock_provider_is_always_simulated_and_tracks_history() -> None:
    p = MockDeployProvider()
    site, previous, report = deploy(p)
    assert previous is None
    assert (report.ok, report.level) == (True, VerificationLevel.SIMULATED)
    assert not acceptable_for_live(report, require_public=True)
    _, previous, _ = deploy(p, 2, "Second")
    assert previous == 1 and p.active_version(HOST) == 2
    assert [c[0] for c in p.calls] == ["publish", "activate", "verify"] * 2


def test_mock_provider_idempotency_conflict_and_failures() -> None:
    p = MockDeployProvider()
    site = rendered()
    assert p.publish(HOST, 1, site).created and not p.publish(HOST, 1, site).created
    with pytest.raises(ReleaseConflict):
        p.publish(HOST, 1, rendered("Changed"))
    p.fail = {"activate"}
    with pytest.raises(StorageError):
        p.activate(HOST, 1)
    p.fail = {"verify"}
    p.activate(HOST, 1)
    assert not p.verify(HOST, 1, site.manifest).ok
    p.fail, p.tamper = set(), True
    assert "hash mismatch" in p.verify(HOST, 1, site.manifest).error


# --- configuration and production guards ------------------------------------------------------


def settings(**kw) -> DeploySettings:  # noqa: ANN003
    base = DeploySettings(
        provider="local",
        base_domain=BASE,
        sites_root="/nonexistent",
        dns_mode="skip",
        http_check=False,
        require_public=False,
        production=False,
        health_timeout_s=60,
    )
    return dataclasses.replace(base, **kw)


PROD_OK = {
    "production": True,
    "require_public": True,
    "dns_mode": "preconfigured",
    "http_check": True,
}


@pytest.mark.parametrize("kw", [{"provider": ""}, {"base_domain": ""}])
def test_deployment_off_when_unconfigured(kw: dict) -> None:
    with pytest.raises(DeployNotConfigured):
        get_provider(settings(**kw))


def test_development_defaults_are_valid(root: Path) -> None:
    assert config_problems(settings()) == []
    p = get_provider(settings(sites_root=str(root)))
    assert isinstance(p, LocalReleaseProvider) and p.verifier is None
    assert isinstance(get_provider(settings(provider="mock")), MockDeployProvider)


def test_production_with_full_public_verification_is_valid(root: Path) -> None:
    transport = serving(root)
    p = get_provider(settings(sites_root=str(root), **PROD_OK), transport=transport)
    assert isinstance(p, LocalReleaseProvider)
    assert isinstance(p.verifier.dns, PreconfiguredDns) and p.verifier.http is not None


@pytest.mark.parametrize(
    ("kw", "problem"),
    [
        (PROD_OK | {"provider": "mock"}, "mock provider is not allowed in production"),
        (PROD_OK | {"dns_mode": "mock"}, "mock DNS is not allowed in production"),
        (PROD_OK | {"dns_mode": "skip"}, "requires SITE_DNS_MODE=preconfigured"),
        (PROD_OK | {"http_check": False}, "requires SITE_HTTP_CHECK=1"),
        (PROD_OK | {"require_public": False, "http_check": False}, "requires SITE_HTTP_CHECK=1"),
        ({"require_public": True}, "requires SITE_DNS_MODE=preconfigured"),
        ({"require_public": True, "provider": "mock"}, "requires DEPLOY_PROVIDER=local"),
        ({"http_check": True}, "SITE_HTTP_CHECK needs a DNS check"),
        ({"provider": "s3"}, "DEPLOY_PROVIDER must be one of"),
        ({"dns_mode": "godaddy"}, "SITE_DNS_MODE must be one of"),
        ({"base_domain": "Example.IN"}, "BASE_DOMAIN is not a valid domain"),
        ({"health_timeout_s": 0}, "SITE_HEALTH_TIMEOUT_S must be positive"),
    ],
)
def test_unsafe_configurations_are_refused(kw: dict, problem: str) -> None:
    assert any(problem in p for p in config_problems(settings(**kw)))
    with pytest.raises(DeployConfigError, match="SITE_|DEPLOY_|BASE_|mock"):
        get_provider(settings(**kw))


def test_production_app_env_always_requires_public(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "APP_ENV", "production")
    monkeypatch.setattr(config, "SITE_REQUIRE_PUBLIC", False)
    monkeypatch.setattr(config, "DEPLOY_PROVIDER", "mock")
    monkeypatch.setattr(config, "BASE_DOMAIN", BASE)
    s = DeploySettings.from_config()
    assert s.production and s.require_public
    with pytest.raises(DeployConfigError, match="not allowed in production"):
        get_provider()


def test_get_provider_never_resolves_or_fetches_during_construction(root: Path) -> None:
    # Building a provider is pure; checks only run in verify().
    get_provider(settings(sites_root=str(root), **PROD_OK))
