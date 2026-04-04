"""Tests for pipeline/rate_limiter.py — LeakyBucketLimiter and shopify_retry."""

from __future__ import annotations

import time
from unittest.mock import MagicMock, call, patch

import pytest
import requests

from pipeline.rate_limiter import LeakyBucketLimiter, RateLimitError, shopify_retry


# ---------------------------------------------------------------------------
# LeakyBucketLimiter tests
# ---------------------------------------------------------------------------


class TestLeakyBucketLimiterNoBlock:
    """wait() with enough tokens available should not sleep."""

    def test_wait_no_sleep_when_tokens_available(self) -> None:
        limiter = LeakyBucketLimiter(pts_per_sec=10.0, bucket_max=1000.0)
        # Pre-fill tokens by simulating time has passed
        limiter._tokens = 50.0
        with patch("time.sleep") as mock_sleep:
            limiter.wait(cost=1)
        mock_sleep.assert_not_called()

    def test_wait_consumes_tokens(self) -> None:
        limiter = LeakyBucketLimiter(pts_per_sec=10.0, bucket_max=1000.0)
        limiter._tokens = 20.0
        limiter.wait(cost=5)
        assert limiter._tokens == pytest.approx(15.0, abs=1.0)


class TestLeakyBucketLimiterBlocking:
    """wait() should sleep when token debt is large (mock time to avoid real sleep)."""

    def test_wait_sleeps_for_large_cost(self) -> None:
        """wait(cost=1000) with pts_per_sec=5 should sleep ~200s (mocked)."""
        limiter = LeakyBucketLimiter(pts_per_sec=5.0, bucket_max=1000.0)
        limiter._tokens = 0.0
        sleep_calls: list[float] = []
        with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            limiter.wait(cost=1000)
        total_sleep = sum(sleep_calls)
        # 1000 cost / 5 pts_per_sec = 200 seconds
        assert total_sleep == pytest.approx(200.0, abs=1.0)

    def test_rapid_waits_drain_bucket_and_sleep(self) -> None:
        """Multiple rapid wait() calls that exhaust tokens must trigger sleep."""
        limiter = LeakyBucketLimiter(pts_per_sec=10.0, bucket_max=100.0)
        limiter._tokens = 0.0
        sleep_calls: list[float] = []
        # Patch monotonic so time doesn't advance between calls
        base_time = 1000.0
        with patch("time.monotonic", return_value=base_time):
            limiter._last_refill = base_time
            with patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
                for _ in range(5):
                    limiter.wait(cost=5)
        assert len(sleep_calls) >= 1, "Should have slept at least once with empty bucket"


class TestLeakyBucketLimiterRecordResponse:
    """record_response() should adjust internal token count from actualQueryCost."""

    def test_record_response_deducts_actual_cost(self) -> None:
        limiter = LeakyBucketLimiter(pts_per_sec=5.0)
        limiter._tokens = 50.0
        extensions = {"actualQueryCost": 10}
        limiter.record_response(extensions)
        assert limiter._tokens == pytest.approx(40.0, abs=0.1)

    def test_record_response_with_none_is_noop(self) -> None:
        limiter = LeakyBucketLimiter(pts_per_sec=5.0)
        limiter._tokens = 50.0
        limiter.record_response(None)
        assert limiter._tokens == pytest.approx(50.0, abs=0.1)

    def test_record_response_missing_key_is_noop(self) -> None:
        limiter = LeakyBucketLimiter(pts_per_sec=5.0)
        limiter._tokens = 50.0
        limiter.record_response({})
        assert limiter._tokens == pytest.approx(50.0, abs=0.1)


# ---------------------------------------------------------------------------
# shopify_retry tests
# ---------------------------------------------------------------------------


def _make_429_response(retry_after: str | None = None) -> requests.Response:
    resp = requests.Response()
    resp.status_code = 429
    if retry_after is not None:
        resp.headers["Retry-After"] = retry_after
    return resp


def _make_400_response() -> requests.Response:
    resp = requests.Response()
    resp.status_code = 400
    return resp


def _make_500_response() -> requests.Response:
    resp = requests.Response()
    resp.status_code = 500
    return resp


class TestShopifyRetrySucceedsOnThirdAttempt:
    """shopify_retry retries on 429 and eventually succeeds."""

    def test_retries_on_429_succeeds_on_third(self) -> None:
        call_count = 0

        @shopify_retry
        def flaky_call() -> str:
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                resp = _make_429_response()
                raise requests.HTTPError(response=resp)
            return "success"

        with patch("pipeline.rate_limiter.time.sleep"):
            result = flaky_call()
        assert result == "success"
        assert call_count == 3


class TestShopifyRetryAfterHeader:
    """shopify_retry reads Retry-After header and waits approximately that long."""

    def test_reads_retry_after_header(self) -> None:
        sleep_calls: list[float] = []
        call_count = 0

        @shopify_retry
        def call_with_retry_after() -> str:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                resp = _make_429_response(retry_after="2")
                raise requests.HTTPError(response=resp)
            return "ok"

        # Patch at the module level so the sleep inside wrapper() is captured
        with patch("pipeline.rate_limiter.time.sleep", side_effect=lambda s: sleep_calls.append(s)):
            result = call_with_retry_after()
        assert result == "ok"
        # Should have slept at least once for ~2 seconds from Retry-After
        assert any(s >= 1.5 for s in sleep_calls), f"Expected ~2s sleep, got: {sleep_calls}"


class TestShopifyRetryExhaustion:
    """shopify_retry raises after 5 failed 429 attempts."""

    def test_raises_after_5_attempts(self) -> None:
        call_count = 0

        @shopify_retry
        def always_429() -> None:
            nonlocal call_count
            call_count += 1
            resp = _make_429_response()
            raise requests.HTTPError(response=resp)

        with patch("time.sleep"):
            with pytest.raises(Exception):
                always_429()

        assert call_count == 5


class TestShopifyRetryNoRetryOn4xx5xx:
    """shopify_retry does NOT retry on 400 or 500 — only 429."""

    def test_no_retry_on_400(self) -> None:
        call_count = 0

        @shopify_retry
        def bad_request() -> None:
            nonlocal call_count
            call_count += 1
            resp = _make_400_response()
            raise requests.HTTPError(response=resp)

        with pytest.raises(requests.HTTPError):
            bad_request()

        assert call_count == 1, "Should not retry on 400"

    def test_no_retry_on_500(self) -> None:
        call_count = 0

        @shopify_retry
        def server_error() -> None:
            nonlocal call_count
            call_count += 1
            resp = _make_500_response()
            raise requests.HTTPError(response=resp)

        with pytest.raises(requests.HTTPError):
            server_error()

        assert call_count == 1, "Should not retry on 500"
