import threading
import time

import httpx


class NCBIAPIError(Exception):
    def __init__(self, message, status_code=None, attempts=None):
        super().__init__(message)
        self.status_code = status_code
        self.attempts = attempts


class RateLimiter:
    def __init__(self, requests_per_second: float):
        self._lock = threading.Lock()
        self._tokens = float(requests_per_second)
        self._max_tokens = float(requests_per_second)
        self._refill_rate = float(requests_per_second)
        self._last_refill = time.monotonic()

    def _refill(self):
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(self._max_tokens, self._tokens + elapsed * self._refill_rate)
        self._last_refill = now

    def acquire(self):
        while True:
            with self._lock:
                self._refill()
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                deficit = 1.0 - self._tokens
                wait = max(0.001, deficit / self._refill_rate)
            time.sleep(wait)


_MAX_RETRIES = 3
_BASE_BACKOFF_MS = 1000


def _retryable_status(status_code: int) -> bool:
    return status_code == 429 or 500 <= status_code < 600


def _retry_after_ms(response: httpx.Response) -> int | None:
    ra = response.headers.get("retry-after")
    if ra is None:
        return None
    try:
        return int(ra) * 1000
    except ValueError:
        return None


def with_retry(rate_limiter: RateLimiter | None, fn, max_retries: int = _MAX_RETRIES):
    for attempt in range(max_retries + 1):
        try:
            return fn()
        except httpx.HTTPStatusError as e:
            if not _retryable_status(e.response.status_code):
                raise
            if attempt >= max_retries:
                raise NCBIAPIError(
                    f"NCBI API request failed after {attempt + 1} attempts (HTTP {e.response.status_code})",
                    status_code=e.response.status_code,
                    attempts=attempt + 1,
                ) from e
            wait_ms = _retry_after_ms(e.response) or _BASE_BACKOFF_MS * (2 ** attempt)
            time.sleep(wait_ms / 1000.0)
            if rate_limiter:
                rate_limiter.acquire()
