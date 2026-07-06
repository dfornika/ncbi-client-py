import pytest

from ncbi_client import NCBIClient
from ncbi_client.async_client import AsyncNCBIClient
from ncbi_client.throttle import AsyncMinIntervalLimiter, MinIntervalLimiter


@pytest.fixture
def client():
    c = NCBIClient(api_key="test-key", tool="test", email="test@example.com")
    # BLAST's real 10s-between-requests policy would make every multi-request
    # test slow; that spacing behavior has its own coverage in test_throttle.py.
    c.blast_rate_limiter = MinIntervalLimiter(0.0)
    yield c
    c.close()


@pytest.fixture
async def async_client():
    c = AsyncNCBIClient(tool="test", email="test@example.com")
    c.blast_rate_limiter = AsyncMinIntervalLimiter(0.0)
    yield c
    await c.aclose()
