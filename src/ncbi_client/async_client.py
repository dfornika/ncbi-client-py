from __future__ import annotations

import os

import httpx

from ncbi_client import blast_async
from ncbi_client.blast import MIN_POLL_INTERVAL
from ncbi_client.throttle import AsyncMinIntervalLimiter


class AsyncNCBIClient:
    """Prototype async counterpart to NCBIClient, covering BLAST only so far.

    BLAST searches can sit idle for minutes waiting on NCBI, which is where
    asyncio actually pays for itself: many sequences can be submitted and
    polled concurrently via asyncio.gather() without one thread per search.
    Eutils/Datasets calls are quick round trips where the sync client is
    sufficient, so they aren't mirrored here (yet).
    """

    def __init__(self, tool: str = "ncbi-client-py", email: str | None = None):
        self.api_key = os.environ.get("NCBI_API_KEY", "")
        self.tool = tool
        self.email = email
        self.blast_rate_limiter = AsyncMinIntervalLimiter(10.0)
        self.http = httpx.AsyncClient(timeout=30.0)

    async def aclose(self):
        await self.http.aclose()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        await self.aclose()

    async def blast(self, sequence, *, program, database, poll_interval=MIN_POLL_INTERVAL, timeout=None, **opts):
        return await blast_async.search(
            self, sequence, program=program, database=database,
            poll_interval=poll_interval, timeout=timeout, **opts,
        )

    async def blast_submit(self, sequence, *, program, database, **opts):
        return await blast_async.submit(self, sequence, program=program, database=database, **opts)

    async def blast_status(self, rid):
        return await blast_async.status(self, rid)

    async def blast_fetch(self, rid, *, format_type="JSON2_S"):
        return await blast_async.fetch(self, rid, format_type=format_type)
