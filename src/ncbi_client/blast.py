from __future__ import annotations

import json
import re
import time
from typing import TYPE_CHECKING

from ncbi_client.throttle import NCBIAPIError, with_retry

if TYPE_CHECKING:
    from ncbi_client.client import NCBIClient

BLAST_BASE_URL = "https://blast.ncbi.nlm.nih.gov/Blast.cgi"

PROGRAMS = {"blastn", "blastp", "blastx", "tblastn", "tblastx"}

# NCBI's usage policy: never poll a single RID more than once a minute.
MIN_POLL_INTERVAL = 60.0


class BlastError(NCBIAPIError):
    pass


def _build_submit_params(sequence: str, program: str, database: str, opts: dict) -> dict:
    if program not in PROGRAMS:
        raise ValueError(f"program must be one of {sorted(PROGRAMS)}, got {program!r}")

    return {
        "CMD": "Put",
        "PROGRAM": program,
        "DATABASE": database,
        "QUERY": sequence,
        **{k.upper(): v for k, v in opts.items()},
    }


def _parse_submit_response(body: str) -> dict:
    rid_match = re.search(r"RID\s*=\s*(\S+)", body)
    if not rid_match:
        raise BlastError("BLAST submission did not return an RID")
    rtoe_match = re.search(r"RTOE\s*=\s*(\S+)", body)

    return {
        "rid": rid_match.group(1),
        "rtoe": int(rtoe_match.group(1)) if rtoe_match else None,
    }


def _parse_status_response(body: str, rid: str) -> str:
    match = re.search(r"Status=(\w+)", body)
    if not match:
        raise BlastError(f"could not determine status for RID {rid}")
    return match.group(1)


def _with_common_params(client, params: dict) -> dict:
    if client.tool:
        params.setdefault("tool", client.tool)
    if client.email:
        params.setdefault("email", client.email)
    return params


def _request(client: NCBIClient, method: str, params: dict) -> str:
    params = _with_common_params(client, params)
    client.blast_rate_limiter.acquire()

    def do_request():
        if method == "POST":
            resp = client.http.post(BLAST_BASE_URL, data=params)
        else:
            resp = client.http.get(BLAST_BASE_URL, params=params)
        resp.raise_for_status()
        return resp.text

    return with_retry(client.blast_rate_limiter, do_request)


def submit(client: NCBIClient, sequence: str, *, program: str, database: str, **opts) -> dict:
    """Submit a search (CMD=Put). Returns {"rid": str, "rtoe": int | None}."""
    params = _build_submit_params(sequence, program, database, opts)
    body = _request(client, "POST", params)
    return _parse_submit_response(body)


def status(client: NCBIClient, rid: str) -> str:
    """Lightweight status check (CMD=Get&FORMAT_OBJECT=SearchInfo). Returns WAITING/READY/FAILED/UNKNOWN."""
    body = _request(client, "GET", {"CMD": "Get", "FORMAT_OBJECT": "SearchInfo", "RID": rid})
    return _parse_status_response(body, rid)


def fetch(client: NCBIClient, rid: str, *, format_type: str = "JSON2_S") -> str:
    """Retrieve raw results for a completed search (CMD=Get&FORMAT_TYPE=...)."""
    return _request(client, "GET", {"CMD": "Get", "RID": rid, "FORMAT_TYPE": format_type})


def _parse_json2(raw: str) -> list[dict]:
    data = json.loads(raw)
    searches = []
    for entry in data.get("BlastOutput2", []):
        report = entry.get("report", entry)
        search_result = report.get("results", {}).get("search", {})

        hits = []
        for hit in search_result.get("hits", []):
            descriptions = hit.get("description") or [{}]
            description = descriptions[0]
            hsps = [
                {
                    "bit_score": hsp.get("bit_score"),
                    "score": hsp.get("score"),
                    "evalue": hsp.get("evalue"),
                    "identity": hsp.get("identity"),
                    "align_len": hsp.get("align_len"),
                    "gaps": hsp.get("gaps"),
                    "query_from": hsp.get("query_from"),
                    "query_to": hsp.get("query_to"),
                    "hit_from": hsp.get("hit_from"),
                    "hit_to": hsp.get("hit_to"),
                    "qseq": hsp.get("qseq"),
                    "hseq": hsp.get("hseq"),
                    "midline": hsp.get("midline"),
                }
                for hsp in hit.get("hsps", [])
            ]
            hits.append({
                "accession": description.get("accession"),
                "title": description.get("title"),
                "taxid": description.get("taxid"),
                "len": hit.get("len"),
                "hsps": hsps,
            })

        searches.append({
            "query_id": search_result.get("query_id"),
            "query_title": search_result.get("query_title"),
            "query_len": search_result.get("query_len"),
            "hits": hits,
        })
    return searches


def search(
    client: NCBIClient,
    sequence: str,
    *,
    program: str,
    database: str,
    poll_interval: float = MIN_POLL_INTERVAL,
    timeout: float | None = None,
    **opts,
) -> dict:
    """Submit a search and block until results are ready, returning parsed hits.

    Smooths over the raw CGI protocol (submit, then poll a shared endpoint by
    scraping "Status=" out of an HTML blob) into a single call.
    """
    poll_interval = max(poll_interval, MIN_POLL_INTERVAL)

    submission = submit(client, sequence, program=program, database=database, **opts)
    rid = submission["rid"]

    if submission["rtoe"]:
        time.sleep(submission["rtoe"])

    deadline = time.monotonic() + timeout if timeout is not None else None
    while True:
        state = status(client, rid)
        if state == "READY":
            break
        if state == "FAILED":
            raise BlastError(f"BLAST search {rid} failed")
        if state == "UNKNOWN":
            raise BlastError(f"BLAST search {rid} is unknown or has expired")
        if deadline is not None and time.monotonic() >= deadline:
            raise BlastError(f"BLAST search {rid} timed out after {timeout}s")
        time.sleep(poll_interval)

    raw = fetch(client, rid, format_type="JSON2_S")
    return {
        "rid": rid,
        "program": program,
        "database": database,
        "searches": _parse_json2(raw),
    }
