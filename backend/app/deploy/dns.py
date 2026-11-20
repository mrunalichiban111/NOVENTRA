"""Read-only DNS checks for site hosts (FR-SITE-4, approved M4B scope). Never writes records.

Modes (SITE_DNS_MODE):
- skip: no lookup. The result says `resolved=False`, so the deployment can never be `public`.
- mock: a deterministic answer for tests and local demos. Marked `simulated`, so it can never
  be `public` either.
- preconfigured: polls the system resolver until the host resolves or the deadline passes.
  The record itself (wildcard or per-site A record) must already exist; creating records through
  the GoDaddy API is deliberately out of scope until separately approved.
"""

import socket
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

Resolver = Callable[[str], list[str]]
MIN_WAIT_S = 0.05


@dataclass(frozen=True)
class DnsResult:
    ok: bool
    resolved: bool  # True only after a real lookup returned addresses
    simulated: bool
    detail: str
    addresses: tuple[str, ...] = field(default_factory=tuple)

    def to_json(self) -> dict:
        return {
            "name": "dns",
            "ok": self.ok,
            "resolved": self.resolved,
            "simulated": self.simulated,
            "detail": self.detail,
            "addresses": list(self.addresses),
        }


class DnsChecker(Protocol):
    mode: str

    def check(self, host: str, deadline: float | None = None) -> DnsResult: ...


class SkipDns:
    mode = "skip"

    def check(self, host: str, deadline: float | None = None) -> DnsResult:
        return DnsResult(ok=True, resolved=False, simulated=False, detail="DNS check skipped")


class MockDns:
    """Answers from a fixed map (default: every host resolves to 192.0.2.1, TEST-NET-1)."""

    mode = "mock"

    def __init__(self, answers: Mapping[str, list[str]] | None = None) -> None:
        self.answers = answers

    def check(self, host: str, deadline: float | None = None) -> DnsResult:
        addrs = ["192.0.2.1"] if self.answers is None else list(self.answers.get(host, []))
        if not addrs:
            return DnsResult(False, False, True, "simulated: host does not resolve")
        return DnsResult(True, False, True, "simulated resolution", tuple(addrs))


def system_resolver(host: str) -> list[str]:
    infos = socket.getaddrinfo(host, 443, proto=socket.IPPROTO_TCP)
    return sorted({info[4][0] for info in infos})


class _Lookup:
    """One resolver call on a daemon thread, so the caller can stop waiting at the deadline.

    getaddrinfo(3) has no timeout parameter and cannot be cancelled, so a hung lookup keeps its
    thread until the system resolver gives up (glibc: `options timeout:N attempts:M` in
    /etc/resolv.conf, typically 5s x 2 per nameserver). The checker never starts a second
    lookup while one is still in flight, so a hung resolver costs at most one thread per check.
    """

    def __init__(self, resolver: Resolver, host: str) -> None:
        self.result: list[str] | None = None
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._run, args=(resolver, host), daemon=True)
        self.thread.start()

    def _run(self, resolver: Resolver, host: str) -> None:
        try:
            self.result = resolver(host)
        except BaseException as exc:  # reported to the caller, never raised in the thread
            self.error = exc

    def wait(self, timeout: float) -> bool:
        # 50 ms floor: join(0) could miss even an instant answer, so the deadline may be
        # overrun by at most this much (plus scheduling jitter).
        self.thread.join(max(MIN_WAIT_S, timeout))
        return not self.thread.is_alive()


class PreconfiguredDns:
    """Resolve-only poll bounded by a deadline (FR-SITE-4).

    The deadline is respected as closely as the platform allows: waits and sleeps are capped at
    the remaining time, and a lookup still running at the deadline is abandoned (see _Lookup).
    The function returns within roughly the deadline plus thread-scheduling jitter; the
    abandoned lookup may finish later in the background and its answer is discarded.
    """

    mode = "preconfigured"

    def __init__(
        self,
        *,
        timeout_s: float,
        interval_s: float = 5.0,
        lookup_timeout_s: float = 5.0,
        resolver: Resolver = system_resolver,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.timeout_s, self.interval_s = timeout_s, interval_s
        self.lookup_timeout_s = lookup_timeout_s
        self.resolver, self.clock, self.sleep = resolver, clock, sleep

    def check(self, host: str, deadline: float | None = None) -> DnsResult:
        deadline = self.clock() + self.timeout_s if deadline is None else deadline
        attempts, last = 0, "not attempted"
        lookup: _Lookup | None = None
        while True:
            if lookup is None:  # never stack lookups behind a hung one
                attempts += 1
                lookup = _Lookup(self.resolver, host)
            remaining = deadline - self.clock()
            if lookup.wait(min(self.lookup_timeout_s, remaining)):
                done, lookup = lookup, None
                if done.error is not None:
                    last = type(done.error).__name__
                elif done.result:
                    return DnsResult(
                        True,
                        True,
                        False,
                        f"resolved after {attempts} attempt(s)",
                        tuple(done.result),
                    )
                else:
                    last = "no addresses"
            else:
                last = "lookup timed out"
            remaining = deadline - self.clock()
            if remaining <= 0 or (lookup is None and remaining < self.interval_s):
                return DnsResult(
                    False, False, False, f"did not resolve after {attempts} attempt(s): {last}"
                )
            if lookup is None:
                self.sleep(min(self.interval_s, remaining))
