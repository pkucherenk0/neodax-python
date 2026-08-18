"""HTTP resilience. two layers:

  1. rate limiter, proactively space calls (avoid 429 first place)
  2. retry loop for 429 / 5xx / network errors, backoff + Retry-After

REQUEST-level retry. separate from test-level retries (kept 0 for trade safety).
429 always safe to retry (rejected before processing). 5xx/network retry configurable so
order-placing (@trades) clients turn it off to avoid double execution.
"""
from __future__ import annotations

import os
import random
import threading
import time
from dataclasses import dataclass
from typing import Any

from playwright.sync_api import APIRequestContext, APIResponse


class RateLimiter:
    """per-process limiter shared across clients. space calls at most `rps` per second.

    NOTE: effective global rate = (parallel workers) x rps. keep rps modest.
    thread lock: xdist workers are separate processes, but keep safe within one.
    """

    def __init__(self, rps: float) -> None:
        self._interval = (1.0 / rps) if rps and rps > 0 else 0.0
        self._next_slot = 0.0
        self._lock = threading.Lock()

    def wait(self) -> None:
        if self._interval <= 0:
            return
        with self._lock:
            now = time.monotonic()
            slot = max(now, self._next_slot)
            self._next_slot = slot + self._interval
            pause = slot - now
        if pause > 0:
            time.sleep(pause)


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(value)
        return max(0.0, dt.timestamp() - time.time())
    except Exception:
        return None


def _backoff(attempt: int) -> float:
    return min(30.0, 0.5 * (2**attempt)) + random.random() * 0.25


DEFAULT_RPS = float(os.environ.get("NIMBUS_RPS", "5"))
DEFAULT_MAX_RETRIES = int(os.environ.get("NIMBUS_MAX_RETRIES", "4"))


@dataclass(frozen=True)
class ResilientOptions:
    max_retries: int
    retry_on_5xx: bool


class ResilientClient:
    """wrap APIRequestContext with rate limit + retry. same get/post surface as raw context."""

    def __init__(self, ctx: APIRequestContext, limiter: RateLimiter, opts: ResilientOptions) -> None:
        self._ctx = ctx
        self._limiter = limiter
        self._opts = opts

    def get(self, url: str, **kwargs: Any) -> APIResponse:
        return self._send(lambda: self._ctx.get(url, **kwargs))

    def post(self, url: str, **kwargs: Any) -> APIResponse:
        return self._send(lambda: self._ctx.post(url, **kwargs))

    def put(self, url: str, **kwargs: Any) -> APIResponse:
        return self._send(lambda: self._ctx.put(url, **kwargs))

    def delete(self, url: str, **kwargs: Any) -> APIResponse:
        return self._send(lambda: self._ctx.delete(url, **kwargs))

    def _send(self, fn) -> APIResponse:
        attempt = 0
        while True:
            self._limiter.wait()
            try:
                res = fn()
                status = res.status
                retryable = status == 429 or (self._opts.retry_on_5xx and 500 <= status <= 599)
                if not retryable or attempt >= self._opts.max_retries:
                    return res
                ra = _parse_retry_after(res.headers.get("retry-after"))
                time.sleep(ra if ra is not None else _backoff(attempt))
            except Exception:
                # network failure (DNS/TLS/connection). retry until budget exhausted.
                if attempt >= self._opts.max_retries:
                    raise
                time.sleep(_backoff(attempt))
            attempt += 1
