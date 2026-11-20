"""Pure tests for real-domain availability (FR-BR-2). No network: httpx.MockTransport only."""

import json
import logging
import threading
import time
from decimal import Decimal

import httpx
import pytest

from app.domains import availability as av

TOKEN = "pat-secret-DO-NOT-LEAK-123"
S = av.DomainStatus


def provider(handler, token: str | None = TOKEN, **kw) -> av.GoDaddyProvider:  # noqa: ANN001, ANN003
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return av.GoDaddyProvider(token, client=client, sleep=lambda _s: None, **kw)


def ok(body) -> callable:  # noqa: ANN001
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=body)

    return handler


AVAILABLE_BODY = {
    "domain": "taxnest.in",
    "available": True,
    "definitive": False,
    "inventory": "registry",
    "prices": [
        {"period": 2, "price": {"value": 3000000, "currencyCode": "INR"}},
        {
            "period": 1,
            "price": {"value": 1499000000, "currencyCode": "INR"},
            "renewalPrice": {"value": 1799000000, "currencyCode": "INR"},
        },
    ],
}


def test_request_shape_and_auth_header() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers["authorization"]
        seen["accept"] = request.headers["accept"]
        seen["method"] = request.method
        return httpx.Response(200, json={"available": False})

    provider(handler).check("taxnest.in")
    assert seen["method"] == "GET"
    assert seen["url"] == (
        "https://api.godaddy.com/v3/domains/check-availability?domain=taxnest.in"
    )
    assert seen["auth"] == f"Bearer {TOKEN}" and seen["accept"] == "application/json"


def test_available_maps_raw_price_and_ignores_definitive_false() -> None:
    r = provider(ok(AVAILABLE_BODY)).check("taxnest.in")
    assert r.status == S.AVAILABLE and r.definitive is False and r.reason is None
    assert r.raw_price == Decimal("1499000000") and r.currency == "INR"  # period 1, unconverted
    assert r.tld == ".in"
    assert av.display_message(r.status, r.reason) == "Likely available — verify on GoDaddy."


def test_unavailable() -> None:
    r = provider(ok({"available": False, "definitive": True})).check("taxnest.com")
    assert (r.status, r.definitive, r.raw_price) == (S.UNAVAILABLE, True, None)


@pytest.mark.parametrize(
    "body",
    [{"available": "yes"}, {"definitive": True}, [], "x", None, {"available": None}],
)
def test_malformed_bodies_are_unknown(body) -> None:  # noqa: ANN001
    r = provider(ok(body)).check("a.in")
    assert (r.status, r.reason) == (S.UNKNOWN, av.MALFORMED)


def test_non_json_is_unknown() -> None:
    r = provider(lambda req: httpx.Response(200, text="<html>")).check("a.in")
    assert (r.status, r.reason) == (S.UNKNOWN, av.MALFORMED)


def test_bad_price_does_not_hide_the_status() -> None:
    body = {"available": True, "prices": [{"period": 1, "price": {"value": "NaN"}}]}
    r = provider(ok(body)).check("a.in")
    assert r.status == S.AVAILABLE and r.raw_price is None


def test_missing_credentials_never_call_out() -> None:
    calls = []
    r = provider(lambda req: calls.append(req) or httpx.Response(200), token="").check("a.in")
    assert (r.status, r.reason, calls) == (S.UNKNOWN, av.NOT_CONFIGURED, [])
    assert av.display_message(r.status, r.reason) == "Availability check is not configured."


def test_timeout_is_unknown_without_retry() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        raise httpx.ReadTimeout("slow", request=request)

    r = provider(handler).check("a.in")
    assert (r.status, r.reason, len(calls)) == (S.UNKNOWN, av.TIMEOUT, 1)


def test_connection_error_is_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    assert provider(handler).check("a.in").reason == av.NETWORK_ERROR


@pytest.mark.parametrize(("code", "reason"), [(429, av.RATE_LIMITED), (503, av.SERVER_ERROR)])
def test_429_and_5xx_retry_twice_then_unknown(code: int, reason: str) -> None:
    calls = []
    sleeps = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(code)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    r = av.GoDaddyProvider(TOKEN, client=client, sleep=sleeps.append).check("a.in")
    assert (r.status, r.reason) == (S.UNKNOWN, reason)
    assert len(calls) == 3 and len(sleeps) == 2 and sleeps[1] > sleeps[0] * 1.0 - 1e-9


def test_retry_then_success() -> None:
    codes = iter([429, 502, 200])

    def handler(request: httpx.Request) -> httpx.Response:
        code = next(codes)
        return httpx.Response(code, json={"available": True} if code == 200 else None)

    assert provider(handler).check("a.in").status == S.AVAILABLE


@pytest.mark.parametrize(
    ("code", "reason"),
    [(401, av.AUTH_FAILED), (403, av.AUTH_FAILED), (404, av.HTTP_ERROR), (422, av.HTTP_ERROR)],
)
def test_other_http_errors_are_unknown_without_retry(code: int, reason: str) -> None:
    calls = []
    r = provider(lambda req: calls.append(1) or httpx.Response(code)).check("a.in")
    assert (r.status, r.reason, len(calls)) == (S.UNKNOWN, reason, 1)


def test_unexpected_exception_is_unknown() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"boom {request.headers['authorization']}")

    r = provider(handler).check("a.in")
    assert (r.status, r.reason) == (S.UNKNOWN, av.INTERNAL_ERROR)


def test_token_never_leaks_into_logs_results_or_repr(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG)

    def handler(request: httpx.Request) -> httpx.Response:
        raise RuntimeError(f"echo {request.headers['authorization']}")

    p = provider(handler)
    results = [p.check("a.in"), provider(lambda r: httpx.Response(401)).check("b.in")]
    blob = caplog.text + repr(p) + json.dumps([repr(r) for r in results])
    assert TOKEN not in blob


UNKNOWN_REASONS = (
    None, av.NOT_CONFIGURED, av.TIMEOUT, av.RATE_LIMITED, av.SERVER_ERROR, av.AUTH_FAILED,
    av.HTTP_ERROR, av.MALFORMED, av.NETWORK_ERROR, av.INTERNAL_ERROR,
)  # fmt: skip


@pytest.mark.parametrize("reason", UNKNOWN_REASONS)
def test_unknown_never_reads_as_available(reason: str | None) -> None:
    msg = av.display_message(S.UNKNOWN, reason)
    assert msg != av.display_message(S.AVAILABLE, None)
    assert "likely available" not in msg.lower() and "available." not in msg.lower()


def test_mock_provider_is_deterministic_and_covers_all_statuses() -> None:
    m = av.MockDomainProvider()
    first = [m.check(f"name{i}.in") for i in range(60)]
    assert first == [m.check(f"name{i}.in") for i in range(60)]
    assert {r.status for r in first} == set(S)
    assert all(r.raw_price is None for r in first if r.status != S.AVAILABLE)


# --- cache ------------------------------------------------------------------------------


class Counting:
    name = "counting"

    def __init__(self, status: av.DomainStatus = S.AVAILABLE, delay: float = 0.0) -> None:
        self.calls: list[str] = []
        self.status, self.delay = status, delay
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0

    def check(self, domain: str) -> av.DomainResult:
        with self._lock:
            self.calls.append(domain)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        time.sleep(self.delay)
        with self._lock:
            self.active -= 1
        if self.status == S.UNKNOWN:
            return av.unknown(domain, av.SERVER_ERROR)
        return av.DomainResult(domain, self.status)


def test_cache_hits_within_ttl_and_expires_after() -> None:
    now = [0.0]
    cache = av.AvailabilityCache(ttl_s=600, clock=lambda: now[0])
    p = Counting()
    assert cache.get_or_check("A.in", p).cached is False
    now[0] = 599
    assert cache.get_or_check("a.in", p).cached is True  # keyed by full, lower-cased domain
    assert p.calls == ["a.in"]
    now[0] = 601
    cache.get_or_check("a.in", p)
    assert len(p.calls) == 2


def test_unknown_is_not_cached() -> None:
    cache = av.AvailabilityCache()
    p = Counting(S.UNKNOWN)
    cache.get_or_check("a.in", p)
    cache.get_or_check("a.in", p)
    assert len(p.calls) == 2


def test_single_flight_shares_one_provider_call() -> None:
    cache = av.AvailabilityCache()
    p = Counting(delay=0.2)
    barrier = threading.Barrier(6)
    results = []

    def go() -> None:
        barrier.wait()
        results.append(cache.get_or_check("same.in", p))

    threads = [threading.Thread(target=go) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(5)
    assert p.calls == ["same.in"] and len(results) == 6
    assert {r.status for r in results} == {S.AVAILABLE}


def test_raising_provider_is_contained() -> None:
    class Bad:
        name = "bad"

        def check(self, domain: str) -> av.DomainResult:
            raise RuntimeError("x")

    r = av.AvailabilityCache().get_or_check("a.in", Bad())
    assert (r.status, r.reason) == (S.UNKNOWN, av.INTERNAL_ERROR)


def test_check_domains_keeps_order_and_caps_concurrency() -> None:
    p = Counting(delay=0.05)
    names = [f"n{i}.in" for i in range(20)]
    out = av.check_domains(names, p, av.AvailabilityCache())
    assert [r.domain for r in out] == names
    assert 1 < p.max_active <= av.CONCURRENCY
