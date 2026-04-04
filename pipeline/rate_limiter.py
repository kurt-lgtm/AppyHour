"""Shopify GraphQL rate limiter and retry policy.

React equivalent: axios interceptor with token bucket + exponential backoff.

Exports:
    LeakyBucketLimiter  — synchronous token-bucket throttle for Shopify cost units
    shopify_retry       — tenacity decorator for 429 retry with jitter
    RateLimitError      — raised internally to trigger tenacity retry
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field

import requests
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
    wait_random,
)

logger = logging.getLogger(__name__)


class RateLimitError(Exception):
    """Raised when a Shopify 429 response is received; triggers tenacity retry."""


@dataclass
class LeakyBucketLimiter:
    """Synchronous leaky-bucket rate limiter for Shopify GraphQL cost units.

    React equivalent: client-side request queue with token refill interval.

    Usage:
        limiter = LeakyBucketLimiter(pts_per_sec=5.0)
        limiter.wait(cost=10)   # blocks until 10 cost units are available
        response = requests.post(...)
        limiter.record_response(response.json().get("extensions", {}).get("cost"))
    """

    pts_per_sec: float = 5.0  # configurable per D-08; default ~50 pts/s / 10 pts/mutation
    bucket_max: float = 1000.0  # Shopify standard plan bucket size
    _tokens: float = field(default=0.0, init=False, repr=False)
    _last_refill: float = field(default_factory=time.monotonic, init=False, repr=False)

    def wait(self, cost: float = 1.0) -> None:
        """Block until `cost` tokens are available, then consume them.

        Refills tokens based on elapsed wall-clock time since last call.
        Caps accumulated tokens at bucket_max to prevent runaway credit.
        Sleeps if token balance is insufficient for the requested cost.
        """
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._last_refill = now

        # Refill tokens based on elapsed time, capped at bucket_max
        self._tokens = min(self._tokens + elapsed * self.pts_per_sec, self.bucket_max)

        # If not enough tokens, sleep to let the bucket refill
        if self._tokens < cost:
            sleep_duration = (cost - self._tokens) / self.pts_per_sec
            time.sleep(sleep_duration)
            self._tokens = 0.0
        else:
            self._tokens -= cost

    def record_response(self, response_extensions: dict | None) -> None:
        """Update bucket from Shopify's extensions.cost.actualQueryCost.

        Call with: limiter.record_response(resp_json.get("extensions", {}).get("cost"))

        If actualQueryCost is present, deduct it from _tokens to keep the
        local bucket in sync with Shopify's server-side bucket state.
        """
        if response_extensions is None:
            return
        actual_cost = response_extensions.get("actualQueryCost")
        if actual_cost is not None:
            self._tokens -= float(actual_cost)


def shopify_retry(func):  # type: ignore[no-untyped-def]
    """Tenacity decorator: retry on 429 with exponential backoff + jitter.

    Reads Retry-After header when present. Falls back to exponential backoff.
    Max 5 retries. Raises after exhaustion.

    Caps Retry-After sleep at 60s to prevent malicious large sleep (T-02-02).

    Usage:
        @shopify_retry
        def call_shopify(...):
            response = requests.post(...)
            if response.status_code == 429:
                raise requests.HTTPError(response=response)
            return response
    """

    @retry(
        retry=retry_if_exception_type(RateLimitError),
        wait=wait_exponential(multiplier=1, min=2, max=60) + wait_random(0, 1),
        stop=stop_after_attempt(5),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=False,
    )
    def wrapper(*args, **kwargs):  # type: ignore[no-untyped-def]
        try:
            return func(*args, **kwargs)
        except requests.HTTPError as exc:
            response = exc.response
            if response is not None and response.status_code == 429:
                retry_after_raw = response.headers.get("Retry-After")
                if retry_after_raw is not None:
                    try:
                        # Cap at 60s to prevent malicious large sleep (T-02-02)
                        wait_secs = min(float(retry_after_raw), 60.0)
                        time.sleep(wait_secs)
                    except ValueError:
                        pass
                raise RateLimitError(
                    f"Shopify 429 rate limit: {response.url if hasattr(response, 'url') else 'unknown'}"
                ) from exc
            # Non-429 HTTP errors are NOT retried — re-raise immediately (T-02-01)
            raise

    return wrapper
