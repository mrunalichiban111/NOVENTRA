"""Read-only DNS checkers and the HTTPS health check. Fake clocks, fake resolvers and
httpx.MockTransport only: no real lookups, no network (conftest blocks real HTTP)."""

import hashlib
import socket

import httpx
import pytest

from app.deploy.dns import MockDns, PreconfiguredDns, SkipDns
from app.deploy.health import HealthReport, HttpChecker, PublicVerifier, acceptable_for_live
from app.models import VerificationLevel

HOST = "tiffo.example.in"
BODY = b"<!DOCTYPE html><html lang=en></html>"
SHA = hashlib.sha256(BODY).hexdigest()


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)
        self.now += s


# --- DNS ------------------------------------------------------------------------------------


def test_skip_never_counts_as_resolved() -> None:
    r = SkipDns().check(HOST)
    assert (r.ok, r.resolved, r.simulated) == (True, False, False)


def test_mock_dns_is_always_simulated() -> None:
    r = MockDns().check(HOST)
    assert (r.ok, r.resolved, r.simulated) == (True, False, True)
    miss = MockDns({"other.example.in": ["192.0.2.9"]}).check(HOST)
    assert (miss.ok, miss.simulated) == (False, True)


def test_preconfigured_polls_until_resolved() -> None:
    clock, answers = FakeClock(), [socket.gaierror("nope"), [], ["203.0.113.7"]]

    def resolver(host: str) -> list[str]:
        assert host == HOST
        out = answers.pop(0)
        if isinstance(out, Exception):
            raise out
        return out

    dns = PreconfiguredDns(
        timeout_s=60, interval_s=5, resolver=resolver, clock=clock, sleep=clock.sleep
    )
    r = dns.check(HOST)
    assert (r.ok, r.resolved, r.simulated, r.addresses) == (True, True, False, ("203.0.113.7",))
    assert clock.sleeps == [5, 5]


def test_preconfigured_gives_up_at_the_deadline() -> None:
    clock = FakeClock()

    def resolver(host: str) -> list[str]:
        raise socket.gaierror("NXDOMAIN")

    dns = PreconfiguredDns(
        timeout_s=30, interval_s=5, resolver=resolver, clock=clock, sleep=clock.sleep
    )
    r = dns.check(HOST)
    assert not r.ok and not r.resolved and "gaierror" in r.detail
    assert clock.now <= 30 and len(clock.sleeps) == 6


# --- HTTPS ----------------------------------------------------------------------------------


def checker(handler, clock: FakeClock | None = None, timeout: float = 20) -> HttpChecker:  # noqa: ANN001
    clock = clock or FakeClock()
    return HttpChecker(
        timeout_s=timeout,
        interval_s=5,
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
    )


def test_https_200_with_matching_hash_passes() -> None:
    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=BODY)

    c = checker(handler).check(HOST, SHA)
    assert c.ok and "hash match" in c.detail
    assert str(seen[0].url) == f"https://{HOST}/"
    assert seen[0].headers["user-agent"].startswith("NOVENTRA-healthcheck")


def test_https_waits_out_propagation_then_passes() -> None:
    replies = [httpx.Response(503), httpx.Response(404), httpx.Response(200, content=BODY)]
    clock = FakeClock()
    c = checker(lambda req: replies.pop(0), clock).check(HOST, SHA)
    assert c.ok and "3 attempt" in c.detail and clock.sleeps == [5, 5]


@pytest.mark.parametrize(
    ("handler", "detail"),
    [
        (lambda req: httpx.Response(200, content=b"stale page"), "does not match"),
        (lambda req: httpx.Response(500), "HTTP 500"),
        (lambda req: httpx.Response(301, headers={"Location": "https://evil.example/"}), "301"),
        (lambda req: httpx.Response(200, content=b"x" * (600 * 1024)), "too large"),
    ],
)
def test_https_failures_are_reported(handler, detail: str) -> None:  # noqa: ANN001
    c = checker(handler).check(HOST, SHA)
    assert not c.ok and detail in c.detail


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectError("TLS handshake failed"),
        httpx.ConnectTimeout("timed out"),
        httpx.ReadTimeout("timed out"),
    ],
)
def test_transport_errors_are_retried_then_fail(exc: Exception) -> None:
    clock = FakeClock()

    def handler(req: httpx.Request) -> httpx.Response:
        raise exc

    c = checker(handler, clock, timeout=10).check(HOST, SHA)
    assert not c.ok and type(exc).__name__ in c.detail
    assert clock.now <= 10


def test_redirects_are_not_followed() -> None:
    calls: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(str(req.url))
        return httpx.Response(302, headers={"Location": f"https://{HOST}/other"})

    checker(handler, timeout=0).check(HOST, SHA)
    assert calls == [f"https://{HOST}/"]


# --- PublicVerifier and the LIVE policy -------------------------------------------------------


def ok_dns() -> PreconfiguredDns:
    return PreconfiguredDns(timeout_s=1, resolver=lambda h: ["203.0.113.7"])


def test_public_requires_real_dns_and_https() -> None:
    http = checker(lambda req: httpx.Response(200, content=BODY))
    assert PublicVerifier(ok_dns(), http, timeout_s=0, clock=lambda: 0.0).verify(HOST, SHA)[:2] == (
        True,
        True,
    )
    assert PublicVerifier(MockDns(), http, timeout_s=0, clock=lambda: 0.0).verify(HOST, SHA)[
        :2
    ] == (True, False)
    assert PublicVerifier(SkipDns(), http, timeout_s=0, clock=lambda: 0.0).verify(HOST, SHA)[
        :2
    ] == (True, False)
    ok, public, checks = PublicVerifier(ok_dns(), None, timeout_s=0, clock=lambda: 0.0).verify(
        HOST, SHA
    )
    assert (ok, public) == (True, False) and checks[-1]["skipped"] is True


def test_dns_failure_stops_before_https() -> None:
    calls: list = []
    http = checker(lambda req: calls.append(req) or httpx.Response(200, content=BODY))
    dns = MockDns({})
    ok, public, checks = PublicVerifier(dns, http, timeout_s=0, clock=lambda: 0.0).verify(HOST, SHA)
    assert (ok, public, calls) == (False, False, [])
    assert [c["name"] for c in checks] == ["dns"]


@pytest.mark.parametrize(
    ("level", "require_public", "expected"),
    [
        (VerificationLevel.PUBLIC, True, True),
        (VerificationLevel.LOCAL, True, False),
        (VerificationLevel.SIMULATED, True, False),
        (VerificationLevel.LOCAL, False, True),
        (VerificationLevel.SIMULATED, False, True),
    ],
)
def test_acceptable_for_live(level, require_public: bool, expected: bool) -> None:  # noqa: ANN001
    report = HealthReport(True, level, ())
    assert acceptable_for_live(report, require_public=require_public) is expected


def test_failed_report_is_never_acceptable() -> None:
    bad = HealthReport(False, None, ({"name": "dns", "ok": False, "detail": "x"},))
    assert not acceptable_for_live(bad, require_public=False)
    assert bad.error == "dns: x"
    assert bad.to_json()["publicly_reachable"] is False


# --- deadline handling ----------------------------------------------------------------------


def test_hung_resolver_does_not_hold_the_check_past_the_deadline() -> None:
    import threading
    import time

    release = threading.Event()
    calls: list[str] = []

    def hung(host: str) -> list[str]:
        calls.append(host)
        release.wait(5)  # a resolver stuck far longer than the deadline
        return ["203.0.113.7"]

    dns = PreconfiguredDns(timeout_s=0.3, interval_s=0.05, lookup_timeout_s=0.1, resolver=hung)
    started = time.monotonic()
    try:
        r = dns.check(HOST)
        elapsed = time.monotonic() - started
    finally:
        release.set()
    assert not r.ok and "lookup timed out" in r.detail
    assert elapsed < 0.3 + 0.25  # deadline + MIN_WAIT_S + scheduling jitter
    assert calls == [HOST]  # never stacks a second lookup behind the hung one


def test_dns_sleeps_never_overrun_the_deadline() -> None:
    clock = FakeClock()

    def resolver(host: str) -> list[str]:
        raise socket.gaierror("NXDOMAIN")

    dns = PreconfiguredDns(
        timeout_s=12, interval_s=5, resolver=resolver, clock=clock, sleep=clock.sleep
    )
    assert not dns.check(HOST).ok
    assert clock.now <= 12 and clock.sleeps == [5, 5]


def test_dns_and_https_share_one_deadline() -> None:
    clock = FakeClock()
    timeouts: list[float] = []

    def slow_resolver(host: str) -> list[str]:
        clock.now += 57  # DNS propagation ate most of the 60s budget
        return ["203.0.113.7"]

    def handler(req: httpx.Request) -> httpx.Response:
        timeouts.append(req.extensions["timeout"]["connect"])
        return httpx.Response(503)

    dns = PreconfiguredDns(timeout_s=999, resolver=slow_resolver, clock=clock, sleep=clock.sleep)
    http = HttpChecker(
        timeout_s=999,
        interval_s=5,
        request_timeout_s=10,
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
    )
    ok, public, checks = PublicVerifier(dns, http, timeout_s=60, clock=clock).verify(HOST, SHA)
    assert (ok, public) == (False, False)
    assert timeouts == [3.0]  # request timeout capped at the remaining 3s, one attempt only
    assert clock.now <= 60 and checks[-1]["name"] == "https"


def test_https_request_timeout_never_exceeds_remaining_time() -> None:
    clock = FakeClock()
    timeouts: list[float] = []

    def handler(req: httpx.Request) -> httpx.Response:
        timeouts.append(req.extensions["timeout"]["read"])
        clock.now += 1
        return httpx.Response(503)

    http = HttpChecker(
        timeout_s=13,
        interval_s=5,
        request_timeout_s=10,
        transport=httpx.MockTransport(handler),
        clock=clock,
        sleep=clock.sleep,
    )
    assert not http.check(HOST, SHA).ok
    assert timeouts == [10.0, 7.0, 1.0]  # 13s budget: 10, then 13-6, then the last 1s
    assert clock.now <= 13
