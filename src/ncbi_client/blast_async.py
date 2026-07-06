from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from ncbi_client.blast import (
    BLAST_BASE_URL,
    MIN_POLL_INTERVAL,
    BlastError,
    _build_submit_params,
    _parse_json2,
    _parse_status_response,
    _parse_submit_response,
    _with_common_params,
)
from ncbi_client.throttle import with_retry_async

if TYPE_CHECKING:
    from ncbi_client.async_client import AsyncNCBIClient

__all__ = ["submit", "status", "fetch", "search"]


async def _request(client: AsyncNCBIClient, method: str, params: dict) -> str:
    params = _with_common_params(client, params)
    await client.blast_rate_limiter.acquire()

    async def do_request():
        if method == "POST":
            resp = await client.http.post(BLAST_BASE_URL, data=params)
        else:
            resp = await client.http.get(BLAST_BASE_URL, params=params)
        resp.raise_for_status()
        return resp.text

    return await with_retry_async(client.blast_rate_limiter, do_request)


async def submit(client: AsyncNCBIClient, sequence: str, *, program: str, database: str, **opts) -> dict:
    """Async twin of blast.submit."""
    params = _build_submit_params(sequence, program, database, opts)
    body = await _request(client, "POST", params)
    return _parse_submit_response(body)


async def status(client: AsyncNCBIClient, rid: str) -> str:
    """Async twin of blast.status."""
    body = await _request(client, "GET", {"CMD": "Get", "FORMAT_OBJECT": "SearchInfo", "RID": rid})
    return _parse_status_response(body, rid)


async def fetch(client: AsyncNCBIClient, rid: str, *, format_type: str = "JSON2_S") -> str:
    """Async twin of blast.fetch."""
    return await _request(client, "GET", {"CMD": "Get", "RID": rid, "FORMAT_TYPE": format_type})


async def search(
    client: AsyncNCBIClient,
    sequence: str,
    *,
    program: str,
    database: str,
    poll_interval: float = MIN_POLL_INTERVAL,
    timeout: float | None = None,
    **opts,
) -> dict:
    """Async twin of blast.search.

    Doesn't block the event loop while waiting, so many searches can be
    submitted and polled concurrently with asyncio.gather(), unlike the
    sync version where each call to search() occupies a whole thread for
    however long that job takes to finish.
    """
    poll_interval = max(poll_interval, MIN_POLL_INTERVAL)

    submission = await submit(client, sequence, program=program, database=database, **opts)
    rid = submission["rid"]

    if submission["rtoe"]:
        await asyncio.sleep(submission["rtoe"])

    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout if timeout is not None else None
    while True:
        state = await status(client, rid)
        if state == "READY":
            break
        if state == "FAILED":
            raise BlastError(f"BLAST search {rid} failed")
        if state == "UNKNOWN":
            raise BlastError(f"BLAST search {rid} is unknown or has expired")
        if deadline is not None and loop.time() >= deadline:
            raise BlastError(f"BLAST search {rid} timed out after {timeout}s")
        await asyncio.sleep(poll_interval)

    raw = await fetch(client, rid, format_type="JSON2_S")
    return {
        "rid": rid,
        "program": program,
        "database": database,
        "searches": _parse_json2(raw),
    }
