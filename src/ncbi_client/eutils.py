from __future__ import annotations

from typing import TYPE_CHECKING

from ncbi_client.throttle import with_retry

if TYPE_CHECKING:
    from ncbi_client.client import NCBIClient

EUTILS_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _request(client: NCBIClient, endpoint: str, params: dict) -> dict:
    params = {**params, "retmode": "json"}
    if client.api_key:
        params["api_key"] = client.api_key
    if client.tool:
        params["tool"] = client.tool
    if client.email:
        params["email"] = client.email

    client.rate_limiter.acquire()

    def do_request():
        resp = client.http.get(f"{EUTILS_BASE_URL}/{endpoint}", params=params)
        resp.raise_for_status()
        return resp.json()

    return with_retry(client.rate_limiter, do_request)


def einfo(client: NCBIClient, db: str | None = None):
    if db is None:
        resp = _request(client, "einfo.fcgi", {})
        return resp["einforesult"]["dblist"]
    else:
        resp = _request(client, "einfo.fcgi", {"db": db})
        return resp["einforesult"]["dbinfo"][0]


def esearch(client: NCBIClient, db: str, term: str, *, retmax=None, retstart=None, sort=None, field=None) -> dict:
    params: dict = {"db": db, "term": term}
    if retmax is not None:
        params["retmax"] = retmax
    if retstart is not None:
        params["retstart"] = retstart
    if sort is not None:
        params["sort"] = sort
    if field is not None:
        params["field"] = field

    resp = _request(client, "esearch.fcgi", params)
    result = resp["esearchresult"]
    return {
        "ids": result["idlist"],
        "count": int(result["count"]),
        "retmax": int(result["retmax"]),
        "retstart": int(result["retstart"]),
    }


def esummary(client: NCBIClient, db: str, ids) -> list[dict]:
    if isinstance(ids, (list, tuple)):
        id_str = ",".join(str(i) for i in ids)
    else:
        id_str = str(ids)

    resp = _request(client, "esummary.fcgi", {"db": db, "id": id_str})
    result = resp.get("result", {})
    uids = result.get("uids", [])
    return [result[uid] for uid in uids]


def elink(client: NCBIClient, dbfrom: str, ids, *, db=None, linkname=None, cmd=None) -> list[dict]:
    if isinstance(ids, (list, tuple)):
        id_str = ",".join(str(i) for i in ids)
    else:
        id_str = str(ids)

    params: dict = {"dbfrom": dbfrom, "id": id_str}
    if db is not None:
        params["db"] = db
    if linkname is not None:
        params["linkname"] = linkname
    if cmd is not None:
        params["cmd"] = cmd

    resp = _request(client, "elink.fcgi", params)
    linksets = []
    for ls in resp.get("linksets", []):
        for lsdb in ls.get("linksetdbs", []):
            linksets.append({
                "dbto": lsdb["dbto"],
                "linkname": lsdb["linkname"],
                "ids": list(lsdb.get("links", [])),
            })
    return linksets


def elink_available(client: NCBIClient, dbfrom: str, ids) -> list[dict]:
    if isinstance(ids, (list, tuple)):
        id_str = ",".join(str(i) for i in ids)
    else:
        id_str = str(ids)

    resp = _request(client, "elink.fcgi", {"dbfrom": dbfrom, "id": id_str, "cmd": "acheck"})
    seen = set()
    results = []
    for ls in resp.get("linksets", []):
        for idlinkset in ls.get("idchecklist", {}).get("idlinksets", []):
            for info in idlinkset.get("linkinfos", []):
                key = (info.get("linkname"), info.get("dbto"))
                if key not in seen:
                    seen.add(key)
                    results.append({
                        "linkname": info.get("linkname"),
                        "dbto": info.get("dbto"),
                        "menutag": info.get("menutag"),
                    })
    return results
