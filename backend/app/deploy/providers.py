"""Deployment-provider abstraction (FR-SITE-3/4/5, M4B R1/R5).

A provider publishes an immutable release, activates it (returning the previous version so a
failed deploy can restore it), reports the active version and verifies a deployment.

- LocalReleaseProvider: ReleaseStore on SITES_ROOT (the volume Caddy will serve read-only),
  plus optional read-only DNS and HTTPS verification.
- MockDeployProvider: in memory, for tests and demos. Always `simulated`, with failure
  injection. Refused in production.

`get_provider` builds the configured provider and enforces the production guards. It raises
DeployNotConfigured when deployment is off (DEPLOY_PROVIDER empty) and DeployConfigError for
any unsafe or contradictory configuration.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from app import config
from app.deploy.dns import DnsChecker, MockDns, PreconfiguredDns, SkipDns
from app.deploy.health import Check, HealthReport, HttpChecker, PublicVerifier
from app.deploy.hosts import HostError, validate_base_domain
from app.deploy.render import RenderedSite
from app.deploy.storage import (
    ReleaseConflict,
    ReleaseNotFound,
    ReleaseRef,
    ReleaseStore,
    StorageError,
    integrity_problems,
)
from app.models import VerificationLevel

PROVIDERS = ("mock", "local")
DNS_MODES = ("skip", "mock", "preconfigured")


class DeployNotConfigured(Exception):
    """Deployment is switched off (DEPLOY_PROVIDER or BASE_DOMAIN empty)."""


class DeployConfigError(Exception):
    """The deployment configuration is invalid or unsafe for this environment."""


class DeployProvider(Protocol):
    name: str

    def publish(self, host: str, version: int, site: RenderedSite) -> ReleaseRef: ...

    def has_release(self, host: str, version: int, artifact_hash: str) -> bool:
        """True if vN exists with this hash; ReleaseConflict if it exists with another."""
        ...

    def activate(self, host: str, version: int) -> int | None: ...

    def active_version(self, host: str) -> int | None: ...

    def verify(self, host: str, version: int, manifest: Mapping[str, Any]) -> HealthReport: ...


def _index_sha(manifest: Mapping[str, Any]) -> str | None:
    try:
        return manifest["files"]["index.html"]["sha256"]
    except (KeyError, TypeError):
        return None


def _manifest_bytes(files: Mapping[str, bytes]) -> Mapping[str, Any] | None:
    try:
        return json.loads(files["manifest.json"])
    except (KeyError, ValueError):
        return None


class LocalReleaseProvider:
    name = "local"

    def __init__(self, store: ReleaseStore, verifier: PublicVerifier | None = None) -> None:
        self.store, self.verifier = store, verifier

    def publish(self, host: str, version: int, site: RenderedSite) -> ReleaseRef:
        return self.store.publish(host, version, site.files, site.artifact_hash)

    def has_release(self, host: str, version: int, artifact_hash: str) -> bool:
        return self.store.check_existing(host, version, artifact_hash) is not None

    def activate(self, host: str, version: int) -> int | None:
        return self.store.activate(host, version)

    def active_version(self, host: str) -> int | None:
        return self.store.active_version(host)

    def verify(self, host: str, version: int, manifest: Mapping[str, Any]) -> HealthReport:
        checks = [c.to_json() for c in self._local_checks(host, version, manifest)]
        if not all(c["ok"] for c in checks):
            return HealthReport(False, None, tuple(checks))
        if self.verifier is None:
            return HealthReport(True, VerificationLevel.LOCAL, tuple(checks))
        ok, public, remote = self.verifier.verify(host, _index_sha(manifest) or "")
        checks += remote
        if not ok:
            return HealthReport(False, None, tuple(checks))
        if public:
            level = VerificationLevel.PUBLIC
        elif any(c.get("simulated") for c in remote):
            level = VerificationLevel.SIMULATED
        else:
            level = VerificationLevel.LOCAL
        return HealthReport(True, level, tuple(checks))

    def _local_checks(self, host: str, version: int, manifest: Mapping[str, Any]) -> list[Check]:
        try:
            active = self.store.active_version(host)
        except StorageError as exc:
            return [Check("active_release", False, type(exc).__name__)]
        if active != version:
            return [Check("active_release", False, f"current is v{active}, expected v{version}")]
        out = [Check("active_release", True, f"current -> v{version}")]
        try:
            files = self.store.read_release(host, version)
        except StorageError as exc:
            return [*out, Check("release_files", False, type(exc).__name__)]
        problems = integrity_problems(files)
        if _manifest_bytes(files) != dict(manifest):
            problems.append("manifest on disk differs from the recorded manifest")
        if problems:
            return [*out, Check("release_files", False, "; ".join(problems))]
        return [*out, Check("release_files", True, f"{len(files)} files match the manifest")]


@dataclass
class MockDeployProvider:
    """In-memory provider. `fail` holds operations that raise ("publish", "activate") or
    report failure ("verify"); `tamper` makes verify see a modified index.html."""

    name: str = "mock"
    releases: dict[str, dict[int, dict[str, bytes]]] = field(default_factory=dict)
    active: dict[str, int] = field(default_factory=dict)
    fail: set[str] = field(default_factory=set)
    tamper: bool = False
    calls: list[tuple[str, str, int]] = field(default_factory=list)

    def publish(self, host: str, version: int, site: RenderedSite) -> ReleaseRef:
        self.calls.append(("publish", host, version))
        if "publish" in self.fail:
            raise StorageError("simulated publish failure")
        per_host = self.releases.setdefault(host, {})
        existing = per_host.get(version)
        if existing is not None:
            stored = hashlib.sha256(existing["manifest.json"]).hexdigest()
            if stored != site.artifact_hash:
                raise ReleaseConflict(f"v{version} already exists with different content")
            return ReleaseRef(host, version, stored, created=False)
        per_host[version] = dict(site.files)
        return ReleaseRef(host, version, site.artifact_hash, created=True)

    def has_release(self, host: str, version: int, artifact_hash: str) -> bool:
        files = self.releases.get(host, {}).get(version)
        if files is None:
            return False
        if hashlib.sha256(files["manifest.json"]).hexdigest() != artifact_hash:
            raise ReleaseConflict(f"v{version} already exists with different content")
        return True

    def activate(self, host: str, version: int) -> int | None:
        self.calls.append(("activate", host, version))
        if "activate" in self.fail:
            raise StorageError("simulated activation failure")
        if version not in self.releases.get(host, {}):
            raise ReleaseNotFound(f"{host} v{version} not found")
        previous = self.active.get(host)
        self.active[host] = version
        return previous

    def active_version(self, host: str) -> int | None:
        return self.active.get(host)

    def verify(self, host: str, version: int, manifest: Mapping[str, Any]) -> HealthReport:
        self.calls.append(("verify", host, version))
        files = dict(self.releases.get(host, {}).get(version, {}))
        if self.tamper and "index.html" in files:
            files["index.html"] = files["index.html"] + b"<!-- tampered -->"
        problems = integrity_problems(files) if files else ["release missing"]
        if self.active.get(host) != version:
            problems.append("not the active release")
        if "verify" in self.fail:
            problems.append("simulated health-check failure")
        check = {
            "name": "simulated",
            "ok": not problems,
            "simulated": True,
            "detail": "; ".join(problems) or "simulated deployment verified",
        }
        level = VerificationLevel.SIMULATED if not problems else None
        return HealthReport(not problems, level, (check,))


# --- configuration and production guards --------------------------------------------


@dataclass(frozen=True)
class DeploySettings:
    provider: str
    base_domain: str
    sites_root: str
    dns_mode: str
    http_check: bool
    require_public: bool
    production: bool
    health_timeout_s: float

    @classmethod
    def from_config(cls) -> "DeploySettings":
        return cls(
            provider=config.DEPLOY_PROVIDER,
            base_domain=config.BASE_DOMAIN,
            sites_root=config.SITES_ROOT,
            dns_mode=config.SITE_DNS_MODE,
            http_check=config.SITE_HTTP_CHECK,
            require_public=config.SITE_REQUIRE_PUBLIC or config.APP_ENV == "production",
            production=config.APP_ENV == "production",
            health_timeout_s=config.SITE_HEALTH_TIMEOUT_S,
        )


def config_problems(s: DeploySettings) -> list[str]:
    """Every reason this configuration is unsafe or cannot reach a verified LIVE state."""
    problems = []
    if s.provider not in PROVIDERS:
        problems.append(f"DEPLOY_PROVIDER must be one of {PROVIDERS}")
    try:
        validate_base_domain(s.base_domain)
    except HostError:
        problems.append("BASE_DOMAIN is not a valid domain")
    if s.dns_mode not in DNS_MODES:
        problems.append(f"SITE_DNS_MODE must be one of {DNS_MODES}")
    if s.http_check and s.dns_mode == "skip":
        problems.append("SITE_HTTP_CHECK needs a DNS check (SITE_DNS_MODE is skip)")
    if not s.health_timeout_s > 0:
        problems.append("SITE_HEALTH_TIMEOUT_S must be positive")
    if s.production:
        if s.provider == "mock":
            problems.append("the mock provider is not allowed in production")
        if s.dns_mode == "mock":
            problems.append("mock DNS is not allowed in production")
    if s.production or s.require_public:
        # LIVE will demand `public`, which needs the local store, a real lookup and HTTPS.
        if s.provider != "local":
            problems.append("public verification requires DEPLOY_PROVIDER=local")
        if s.dns_mode != "preconfigured":
            problems.append("public verification requires SITE_DNS_MODE=preconfigured")
        if not s.http_check:
            problems.append("public verification requires SITE_HTTP_CHECK=1")
    return problems


def _dns(s: DeploySettings) -> DnsChecker:
    if s.dns_mode == "mock":
        return MockDns()
    if s.dns_mode == "preconfigured":
        return PreconfiguredDns(timeout_s=s.health_timeout_s)
    return SkipDns()


def get_provider(
    settings: DeploySettings | None = None,
    *,
    transport: Any = None,
    dns: DnsChecker | None = None,
) -> DeployProvider:
    """Build the configured provider. `transport`/`dns` are injection points for tests."""
    s = settings or DeploySettings.from_config()
    if not s.provider or not s.base_domain:
        raise DeployNotConfigured("deployment is not configured (DEPLOY_PROVIDER/BASE_DOMAIN)")
    problems = config_problems(s)
    if problems:
        raise DeployConfigError("; ".join(problems))
    if s.provider == "mock":
        return MockDeployProvider()
    checker = dns or _dns(s)
    http = HttpChecker(timeout_s=s.health_timeout_s, transport=transport) if s.http_check else None
    verifier = (
        None
        if isinstance(checker, SkipDns) and http is None
        else PublicVerifier(checker, http, timeout_s=s.health_timeout_s)
    )
    return LocalReleaseProvider(ReleaseStore(s.sites_root, s.base_domain), verifier)
