import pytest

from ncbi_client import NCBIClient
from ncbi_client.async_client import AsyncNCBIClient


@pytest.fixture
def client():
    c = NCBIClient(api_key="test-key", tool="test", email="test@example.com")
    yield c
    c.close()


@pytest.fixture
async def async_client():
    c = AsyncNCBIClient(tool="test", email="test@example.com")
    yield c
    await c.aclose()
