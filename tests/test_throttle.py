import time

import httpx
import pytest

from ncbi_client.throttle import NCBIAPIError, RateLimiter, with_retry


def test_rate_limiter_acquire():
    rl = RateLimiter(10.0)
    for _ in range(10):
        rl.acquire()


def test_rate_limiter_blocks_when_exhausted():
    rl = RateLimiter(5.0)
    for _ in range(5):
        rl.acquire()
    start = time.monotonic()
    rl.acquire()
    elapsed = time.monotonic() - start
    assert elapsed >= 0.1


def test_with_retry_success():
    result = with_retry(None, lambda: 42)
    assert result == 42


def test_with_retry_raises_non_retryable():
    resp = httpx.Response(400, request=httpx.Request("GET", "http://example.com"))

    def fail():
        raise httpx.HTTPStatusError("bad", request=resp.request, response=resp)

    with pytest.raises(httpx.HTTPStatusError):
        with_retry(None, fail)


def test_with_retry_retries_429():
    call_count = 0

    def flaky():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            resp = httpx.Response(429, request=httpx.Request("GET", "http://example.com"))
            raise httpx.HTTPStatusError("rate limited", request=resp.request, response=resp)
        return "ok"

    rl = RateLimiter(100.0)
    result = with_retry(rl, flaky)
    assert result == "ok"
    assert call_count == 3


def test_with_retry_exhausted():
    def always_fail():
        resp = httpx.Response(500, request=httpx.Request("GET", "http://example.com"))
        raise httpx.HTTPStatusError("server error", request=resp.request, response=resp)

    with pytest.raises(NCBIAPIError) as exc_info:
        with_retry(None, always_fail, max_retries=1)
    assert exc_info.value.status_code == 500
    assert exc_info.value.attempts == 2
