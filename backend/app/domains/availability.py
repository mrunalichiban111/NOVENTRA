"""Read-only real-domain availability (FR-BR-2, FR-BR-4). Fail-soft by design.

Every failure (missing credentials, timeout, 429, 5xx, other HTTP errors, malformed JSON,
exceptions) becomes UNKNOWN with a stable reason code. UNKNOWN is never AVAILABLE. NOVENTRA
never buys domains: this module only issues GET check-availability requests.

The GoDaddy personal access token is a secret. It is sent only in the Authorization header and
never appears in logs, exceptions, results or API responses.

Caching: an in-process TTL cache keyed by full domain, with single-flight so simultaneous
requests for the same domain share one provider call. Only definitive answers (AVAILABLE,
UNAVAILABLE) are cached; UNKNOWN is not, so a later request can recover.
"""

import hashlib
import logging
import random
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any, Protocol

import httpx

from app import config

logger = logging.getLogger(__name__)

GODADDY_URL = "https://api.godaddy.com/v3/domains/check-availability"
TIMEOUT_S = 5.0
MAX_RETRIES = 2  # after the first try, on 429 / 5xx only
BACKOFF_BASE_S = 0.5
CACHE_TTL_S = 600.0
CONCURRENCY = 8
TOP_N = 5  # only the top five ranked valid candidates are checked


class DomainStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNKNOWN = "UNKNOWN"


# Reason codes for UNKNOWN.
NOT_CONFIGURED = "not_configured"
TIMEOUT = "timeout"
RATE_LIMITED = "rate_limited"
SERVER_ERROR = "server_error"
AUTH_FAILED = "auth_failed"
HTTP_ERROR = "http_error"
MALFORMED = "malformed"
NETWORK_ERROR = "network_error"
INTERNAL_ERROR = "internal_error"

MESSAGES = {
    DomainStatus.AVAILABLE: "Likely available — verify on GoDaddy.",
    DomainStatus.UNAVAILABLE: "Not available to register.",
}
UNKNOWN_MESSAGES = {
    NOT_CONFIGURED: "Availability check is not configured.",
    RATE_LIMITED: "The registrar is busy. Try again in a few minutes.",
}
UNKNOWN_DEFAULT = "Could not check availability right now. Try again later."


def display_message(status: str, reason: str | None) -> str:
    """Safe user-facing text. UNKNOWN never reads as available."""
    if status == DomainStatus.UNKNOWN:
        return UNKNOWN_MESSAGES.get(reason or "", UNKNOWN_DEFAULT)
    return MESSAGES[DomainStatus(status)]


@dataclass(frozen=True)
class DomainResult:
    domain: str
    status: DomainStatus
    reason: str | None = None
    raw_price: Decimal | None = None  # units unverified; never converted or guaranteed
    currency: str | None = None
    definitive: bool | None = None
    cached: bool = False

    @property
    def tld(self) -> str:
        return "." + self.domain.rsplit(".", 1)[-1]


def unknown(domain: str, reason: str) -> DomainResult:
    return DomainResult(domain, DomainStatus.UNKNOWN, reason)


class DomainAvailabilityProvider(Protocol):
    name: str

    def check(self, domain: str) -> DomainResult:
        """Must not raise; every failure is UNKNOWN."""
        ...


# --- GoDaddy ---------------------------------------------------------------------------


def _decimal(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return None
    try:
        d = Decimal(str(value))
    except InvalidOperation:
        return None
    return d if d.is_finite() and d >= 0 else None


def _price(body: dict[str, Any]) -> tuple[Decimal | None, str | None]:
    """First-year price from `prices[]` (period 1 preferred). Stored raw, never converted."""
    prices = body.get("prices")
    if not isinstance(prices, list):
        return None, None
    entries = [p for p in prices if isinstance(p, dict)]
    entry = next((p for p in entries if p.get("period") == 1), entries[0] if entries else None)
    if entry is None:
        return None, None
    price = entry.get("price")
    value = price.get("value") if isinstance(price, dict) else price
    currency = (
        (price.get("currencyCode") if isinstance(price, dict) else None)
        or entry.get("currencyCode")
        or body.get("currencyCode")
    )
    currency = currency if isinstance(currency, str) and 0 < len(currency) <= 8 else None
    return _decimal(value), currency


def parse_godaddy(domain: str, body: Any) -> DomainResult:
    """Map a 200 response body. Anything unexpected is UNKNOWN/malformed."""
    if not isinstance(body, dict) or not isinstance(body.get("available"), bool):
        return unknown(domain, MALFORMED)
    definitive = body.get("definitive")
    definitive = definitive if isinstance(definitive, bool) else None
    # available=true is "likely available" regardless of `definitive` (shown in the message).
    status = DomainStatus.AVAILABLE if body["available"] else DomainStatus.UNAVAILABLE
    raw_price, currency = _price(body)
    return DomainResult(domain, status, None, raw_price, currency, definitive)


class GoDaddyProvider:
    name = "godaddy"

    def __init__(
        self,
        token: str | None = None,
        *,
        client: httpx.Client | None = None,
        timeout_s: float = TIMEOUT_S,
        max_retries: int = MAX_RETRIES,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._token = token if token is not None else config.GODADDY_PAT
        self._client = client
        self._timeout = timeout_s
        self._max_retries = max_retries
        self._sleep = sleep

    def __repr__(self) -> str:  # never include the token
        return "GoDaddyProvider(token=<redacted>)"

    def check(self, domain: str) -> DomainResult:
        if not self._token:
            return unknown(domain, NOT_CONFIGURED)
        try:
            return self._check(domain)
        except Exception as exc:  # fail soft; log the type only (messages may echo headers)
            logger.warning("domain check %s failed: %s", domain, type(exc).__name__)
            return unknown(domain, INTERNAL_ERROR)

    def _check(self, domain: str) -> DomainResult:
        headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json"}
        client = self._client or httpx.Client(timeout=self._timeout)
        try:
            reason = INTERNAL_ERROR
            for attempt in range(self._max_retries + 1):
                try:
                    resp = client.get(
                        GODADDY_URL,
                        params={"domain": domain},
                        headers=headers,
                        timeout=self._timeout,
                    )
                except httpx.TimeoutException:
                    return unknown(domain, TIMEOUT)
                except httpx.HTTPError as exc:
                    logger.warning(
                        "domain check %s transport error: %s", domain, type(exc).__name__
                    )
                    return unknown(domain, NETWORK_ERROR)
                code = resp.status_code
                if code == 200:
                    try:
                        body = resp.json()
                    except ValueError:
                        return unknown(domain, MALFORMED)
                    return parse_godaddy(domain, body)
                if code == 429 or code >= 500:
                    reason = RATE_LIMITED if code == 429 else SERVER_ERROR
                    if attempt < self._max_retries:
                        self._sleep(BACKOFF_BASE_S * (2**attempt) * (0.5 + random.random() / 2))
                        continue
                    break
                logger.warning("domain check %s: HTTP %s", domain, code)
                return unknown(domain, AUTH_FAILED if code in (401, 403) else HTTP_ERROR)
            logger.warning("domain check %s gave up: %s", domain, reason)
            return unknown(domain, reason)
        finally:
            if self._client is None:
                client.close()


# --- Mock ------------------------------------------------------------------------------


class MockDomainProvider:
    """Deterministic, offline results for tests and MOCK mode (hash of the domain)."""

    name = "mock"

    def check(self, domain: str) -> DomainResult:
        h = int(hashlib.sha256(domain.encode()).hexdigest()[:8], 16)
        bucket = h % 10
        if bucket == 0:
            return unknown(domain, SERVER_ERROR)
        if bucket < 6:
            return DomainResult(
                domain,
                DomainStatus.AVAILABLE,
                raw_price=Decimal(799 + h % 900),
                currency="INR",
                definitive=bool(h & 1),
            )
        return DomainResult(domain, DomainStatus.UNAVAILABLE, definitive=True)


def get_provider() -> DomainAvailabilityProvider:
    return MockDomainProvider() if config.DOMAIN_CHECK_MOCK else GoDaddyProvider()


# --- Cache with single-flight ----------------------------------------------------------


class AvailabilityCache:
    def __init__(self, ttl_s: float = CACHE_TTL_S, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_s
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: dict[str, tuple[float, DomainResult]] = {}
        self._inflight: dict[str, Future[DomainResult]] = {}

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()

    def get_or_check(self, domain: str, provider: DomainAvailabilityProvider) -> DomainResult:
        key = domain.lower()
        with self._lock:
            hit = self._entries.get(key)
            if hit and self._clock() - hit[0] < self._ttl:
                return replace(hit[1], cached=True)
            waiting = self._inflight.get(key)
            if waiting is None:
                future: Future[DomainResult] = Future()
                self._inflight[key] = future
        if waiting is not None:
            return waiting.result()

        try:
            result = provider.check(key)
        except Exception as exc:  # providers must not raise; stay fail-soft anyway
            logger.warning("domain check %s raised %s", key, type(exc).__name__)
            result = unknown(key, INTERNAL_ERROR)
        with self._lock:
            if result.status != DomainStatus.UNKNOWN:
                self._entries[key] = (self._clock(), result)
            self._inflight.pop(key, None)
        future.set_result(result)
        return result


cache = AvailabilityCache()
_slots = threading.BoundedSemaphore(CONCURRENCY)  # process-wide cap on provider calls


def check_domains(
    domains: Iterable[str],
    provider: DomainAvailabilityProvider | None = None,
    cache_: AvailabilityCache | None = None,
) -> list[DomainResult]:
    """Check domains concurrently (at most CONCURRENCY provider calls in flight). Order kept."""
    provider = provider or get_provider()
    store = cache_ or cache
    names = list(domains)

    def one(domain: str) -> DomainResult:
        with _slots:
            return store.get_or_check(domain, provider)

    if not names:
        return []
    with ThreadPoolExecutor(max_workers=min(CONCURRENCY, len(names))) as pool:
        return list(pool.map(one, names))
