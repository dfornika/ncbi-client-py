import asyncio

import pytest
import respx
from httpx import Response

from ncbi_client import blast_async
from ncbi_client.blast import BlastError
from ncbi_client.throttle import AsyncMinIntervalLimiter
from tests.test_blast import JSON2_S_RESPONSE

BLAST_URL = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"


async def test_submit_extracts_rid_and_rtoe(async_client):
    with respx.mock:
        respx.post(BLAST_URL).mock(
            return_value=Response(200, text="stuff\n    RID = ABC123XYZ\n    RTOE = 15\nmore stuff")
        )
        result = await blast_async.submit(async_client, "ACGT", program="blastn", database="core_nt")
    assert result == {"rid": "ABC123XYZ", "rtoe": 15}


async def test_submit_rejects_unknown_program(async_client):
    with pytest.raises(ValueError):
        await blast_async.submit(async_client, "ACGT", program="not-a-real-program", database="core_nt")


async def test_status_ready(async_client):
    with respx.mock:
        respx.get(BLAST_URL).mock(
            return_value=Response(200, text="QBlastInfoBegin\n    Status=READY\nQBlastInfoEnd")
        )
        assert await blast_async.status(async_client, "ABC123XYZ") == "READY"


async def test_fetch_returns_raw_body(async_client):
    with respx.mock:
        respx.get(BLAST_URL).mock(return_value=Response(200, text=JSON2_S_RESPONSE))
        raw = await blast_async.fetch(async_client, "ABC123XYZ")
    assert raw == JSON2_S_RESPONSE


async def test_search_blocks_until_ready_and_returns_parsed_results(async_client, monkeypatch):
    async def instant_sleep(_):
        pass

    monkeypatch.setattr(blast_async.asyncio, "sleep", instant_sleep)

    statuses = iter(["WAITING", "READY"])

    with respx.mock:
        respx.post(BLAST_URL).mock(return_value=Response(200, text="RID = ABC123XYZ\nRTOE = 5\n"))

        def get_side_effect(request):
            params = dict(request.url.params)
            if params.get("FORMAT_OBJECT") == "SearchInfo":
                return Response(200, text=f"Status={next(statuses)}")
            return Response(200, text=JSON2_S_RESPONSE)

        respx.get(BLAST_URL).mock(side_effect=get_side_effect)

        result = await blast_async.search(async_client, "ACGT", program="blastn", database="core_nt")

    assert result["rid"] == "ABC123XYZ"
    assert len(result["searches"]) == 1


async def test_search_raises_on_failed_status(async_client, monkeypatch):
    async def instant_sleep(_):
        pass

    monkeypatch.setattr(blast_async.asyncio, "sleep", instant_sleep)

    with respx.mock:
        respx.post(BLAST_URL).mock(return_value=Response(200, text="RID = ABC123XYZ\n"))
        respx.get(BLAST_URL).mock(return_value=Response(200, text="Status=FAILED"))

        with pytest.raises(BlastError):
            await blast_async.search(async_client, "ACGT", program="blastn", database="core_nt")


async def test_concurrent_searches_overlap_in_flight(async_client):
    """Demonstrates the actual payoff of async here: two searches' network
    calls interleave on one event loop instead of one search fully finishing
    before the next starts, the way the sync client necessarily would.
    """
    # The default 10s-between-requests policy would dominate this test's
    # runtime and mask any concurrency; the demo isn't about the rate limit.
    async_client.blast_rate_limiter = AsyncMinIntervalLimiter(0.0)

    in_flight = 0
    max_in_flight = 0

    async def side_effect(request):
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.03)
        in_flight -= 1
        if request.method == "POST":
            return Response(200, text="RID = SOMERID\nRTOE = 0\n")
        if dict(request.url.params).get("FORMAT_OBJECT") == "SearchInfo":
            return Response(200, text="Status=READY")
        return Response(200, text=JSON2_S_RESPONSE)

    with respx.mock:
        respx.post(BLAST_URL).mock(side_effect=side_effect)
        respx.get(BLAST_URL).mock(side_effect=side_effect)

        start = asyncio.get_event_loop().time()
        await asyncio.gather(
            blast_async.search(async_client, "ACGT", program="blastn", database="core_nt"),
            blast_async.search(async_client, "TTGG", program="blastn", database="core_nt"),
        )
        elapsed = asyncio.get_event_loop().time() - start

    # Two searches x 3 requests each x 0.03s = 0.18s if fully serialized;
    # concurrent execution should land close to a single search's 0.09s.
    assert max_in_flight >= 2
    assert elapsed < 0.15
