"""Deployment verification (FR-SITE-4, M4B R1). Never executes or renders artifacts.

Verification levels (models.VerificationLevel):
- simulated: mock provider, or any check answered by a mock (mock DNS).
- local: the active symlink points at vN and every file on disk matches the manifest.
         DNS was skipped and/or HTTPS was not checked: NOT evidence the site is online.
- public: local, plus a real DNS lookup resolved the host, plus GET https://<host>/ returned
          200 with a body whose sha256 equals the manifest's index.html hash.

`acceptable_for_live` applies the policy: with SITE_REQUIRE_PUBLIC (always on in production)
only `public` may move a business to LIVE.
"""

import hashlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.deploy.dns import DnsChecker, DnsResult
from app.models import VerificationLevel

MAX_BODY_BYTES = 512 * 1024
MIN_REQUEST_TIMEOUT_S = 0.5
USER_AGENT = "NOVENTRA-healthcheck/1"


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str
    extra: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail, **self.extra}


@dataclass(frozen=True)
class HealthReport:
    ok: bool
    level: VerificationLevel | None  # None when verification failed
    checks: tuple[dict[str, Any], ...]

    @property
    def error(self) -> str | None:
        if self.ok:
            return None
        failed = [f"{c['name']}: {c['detail']}" for c in self.checks if not c["ok"]]
        return "; ".join(failed) or "verification failed"

    def to_json(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "level": self.level.value if self.level else None,
            "publicly_reachable": self.level == VerificationLevel.PUBLIC,
            "checks": list(self.checks),
        }


def acceptable_for_live(report: HealthReport, *, require_public: bool) -> bool:
    if not report.ok or report.level is None:
        return False
    return report.level == VerificationLevel.PUBLIC if require_public else True


class HttpChecker:
    """GET https://<host>/ until 200 + matching body hash, or the deadline passes.

    TLS is verified (httpx default), redirects are not followed, the body is capped, and only
    the status, a size and a hash are recorded (never the body).
    """

    def __init__(
        self,
        *,
        timeout_s: float,
        interval_s: float = 5.0,
        request_timeout_s: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout_s, self.interval_s = timeout_s, interval_s
        self.request_timeout_s = request_timeout_s
        self.transport = transport
        self.clock, self.sleep = clock, sleep

    def _fetch(self, client: httpx.Client, url: str, timeout: float) -> tuple[int, str | None, str]:
        with client.stream("GET", url, timeout=timeout) as resp:
            if resp.status_code != 200:
                return resp.status_code, None, f"HTTP {resp.status_code}"
            digest, size = hashlib.sha256(), 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > MAX_BODY_BYTES:
                    return resp.status_code, None, "body too large"
                digest.update(chunk)
            return resp.status_code, digest.hexdigest(), "HTTP 200"

    def check(self, host: str, expected_sha256: str, deadline: float | None = None) -> Check:
        """Poll until success or `deadline` (monotonic). Each request's timeout is capped at the
        remaining time. httpx applies it per phase (connect, write, read, pool) and the read
        timeout per chunk, so one slow request can overrun the deadline by a bounded multiple
        of the remaining time; it can never start a new attempt after the deadline."""
        url = f"https://{host}/"
        deadline = self.clock() + self.timeout_s if deadline is None else deadline
        attempts, last = 0, "not attempted"
        with httpx.Client(
            transport=self.transport,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Cache-Control": "no-cache"},
        ) as client:
            while True:
                attempts += 1
                remaining = deadline - self.clock()
                timeout = max(MIN_REQUEST_TIMEOUT_S, min(self.request_timeout_s, remaining))
                try:
                    status, body_sha, last = self._fetch(client, url, timeout)
                except httpx.HTTPError as exc:  # DNS, TLS, connect and timeout errors
                    status, body_sha, last = 0, None, type(exc).__name__
                if status == 200 and body_sha is not None:
                    if body_sha == expected_sha256:
                        return Check(
                            "https", True, f"200 and hash match after {attempts} attempt(s)"
                        )
                    last = "served body does not match the release"
                remaining = deadline - self.clock()
                if remaining < self.interval_s:
                    return Check("https", False, f"{last} after {attempts} attempt(s)")
                self.sleep(min(self.interval_s, remaining))


class PublicVerifier:
    """DNS then HTTPS under ONE overall deadline (SITE_HEALTH_TIMEOUT_S), so slow DNS leaves
    less time for HTTPS instead of doubling the wait. `public` is granted only for a real
    lookup plus a real HTTPS match."""

    def __init__(
        self,
        dns: DnsChecker,
        http: HttpChecker | None,
        *,
        timeout_s: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.dns, self.http = dns, http
        self.timeout_s, self.clock = timeout_s, clock

    def verify(self, host: str, expected_index_sha: str) -> tuple[bool, bool, list[dict]]:
        """Returns (ok, public, checks)."""
        deadline = self.clock() + self.timeout_s
        dns: DnsResult = self.dns.check(host, deadline)
        checks = [dns.to_json()]
        if not dns.ok:
            return False, False, checks
        if self.http is None:
            checks.append(
                Check("https", True, "HTTPS check disabled").to_json() | {"skipped": True}
            )
            return True, False, checks
        https = self.http.check(host, expected_index_sha, deadline)
        checks.append(https.to_json())
        if not https.ok:
            return False, False, checks
        return True, dns.resolved and not dns.simulated, checks
