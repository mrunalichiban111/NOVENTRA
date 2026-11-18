"""Small in-process sliding-window limiter for public write endpoints.

Best effort: state is per process (the API runs a single uvicorn process today).
Behind Caddy the client IP comes from X-Forwarded-For, which Caddy sets itself.
"""

import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request

from app.config import RATE_LIMIT_PER_MINUTE


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float = 60.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter(RATE_LIMIT_PER_MINUTE)


def client_key(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[-1].strip()
    return request.client.host if request.client else "unknown"


def rate_limit(request: Request) -> None:
    if not limiter.check(client_key(request)):
        raise HTTPException(status_code=429, detail="Too many requests; slow down.")
