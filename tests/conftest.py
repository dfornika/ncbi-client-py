import pytest

from ncbi_client import NCBIClient


@pytest.fixture
def client():
    c = NCBIClient(api_key="test-key", tool="test", email="test@example.com")
    yield c
    c.close()
