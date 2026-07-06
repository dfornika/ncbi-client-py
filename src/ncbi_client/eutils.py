from __future__ import annotations

from typing import TYPE_CHECKING
from xml.etree import ElementTree

from ncbi_client.throttle import NCBIAPIError, with_retry

if TYPE_CHECKING:
    from ncbi_client.client import NCBIClient

EUTILS_BASE_URL = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"


def _ids_to_str(ids) -> str:
    if isinstance(ids, (list, tuple)):
        return ",".join(str(i) for i in ids)
    return str(ids)


def _get(client: NCBIClient, endpoint: str, params: dict, method: str = "GET"):
    params = dict(params)
    if client.api_key:
        params["api_key"] = client.api_key
    if client.tool:
        params["tool"] = client.tool
    if client.email:
        params["email"] = client.email

    client.rate_limiter.acquire()

    def do_request():
        if method == "POST":
            resp = client.http.post(f"{EUTILS_BASE_URL}/{endpoint}", data=params)
        else:
            resp = client.http.get(f"{EUTILS_BASE_URL}/{endpoint}", params=params)
        resp.raise_for_status()
        return resp

    return with_retry(client.rate_limiter, do_request)


def _request(client: NCBIClient, endpoint: str, params: dict) -> dict:
    return _get(client, endpoint, {**params, "retmode": "json"}).json()


def _request_text(client: NCBIClient, endpoint: str, params: dict, method: str = "GET") -> str:
    # Unlike _request, this doesn't force retmode: efetch's retmode is a real,
    # caller-meaningful choice (text/xml/asn.1), and epost ignores it entirely.
    return _get(client, endpoint, params, method=method).text


def einfo(client: NCBIClient, db: str | None = None):
    if db is None:
        resp = _request(client, "einfo.fcgi", {})
        return resp["einforesult"]["dblist"]
    else:
        resp = _request(client, "einfo.fcgi", {"db": db})
        return resp["einforesult"]["dbinfo"][0]


def esearch(
    client: NCBIClient,
    db: str,
    term: str,
    *,
    retmax=None,
    retstart=None,
    sort=None,
    field=None,
    usehistory: bool = False,
    webenv: str | None = None,
    query_key=None,
) -> dict:
    params: dict = {"db": db, "term": term}
    if retmax is not None:
        params["retmax"] = retmax
    if retstart is not None:
        params["retstart"] = retstart
    if sort is not None:
        params["sort"] = sort
    if field is not None:
        params["field"] = field
    if usehistory:
        params["usehistory"] = "y"
    if webenv is not None:
        params["WebEnv"] = webenv
    if query_key is not None:
        params["query_key"] = query_key

    resp = _request(client, "esearch.fcgi", params)
    result = resp["esearchresult"]
    output = {
        "ids": result["idlist"],
        "count": int(result["count"]),
        "retmax": int(result["retmax"]),
        "retstart": int(result["retstart"]),
    }
    if "webenv" in result:
        output["webenv"] = result["webenv"]
    if "querykey" in result:
        output["query_key"] = int(result["querykey"])
    return output


def esummary(client: NCBIClient, db: str, ids=None, *, webenv: str | None = None, query_key=None) -> list[dict]:
    if ids is None and (webenv is None or query_key is None):
        raise ValueError("esummary requires either ids or both webenv and query_key")

    params: dict = {"db": db}
    if ids is not None:
        params["id"] = _ids_to_str(ids)
    else:
        params["WebEnv"] = webenv
        params["query_key"] = query_key

    resp = _request(client, "esummary.fcgi", params)
    result = resp.get("result", {})
    uids = result.get("uids", [])
    return [result[uid] for uid in uids]


def elink(
    client: NCBIClient, dbfrom: str, ids=None, *, db=None, linkname=None, cmd=None,
    webenv: str | None = None, query_key=None,
) -> list[dict]:
    if ids is None and (webenv is None or query_key is None):
        raise ValueError("elink requires either ids or both webenv and query_key")

    params: dict = {"dbfrom": dbfrom}
    if ids is not None:
        params["id"] = _ids_to_str(ids)
    else:
        params["WebEnv"] = webenv
        params["query_key"] = query_key
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
    resp = _request(client, "elink.fcgi", {"dbfrom": dbfrom, "id": _ids_to_str(ids), "cmd": "acheck"})
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


def efetch(
    client: NCBIClient,
    db: str,
    ids=None,
    *,
    rettype: str,
    webenv: str | None = None,
    query_key=None,
    retmode: str = "text",
) -> str:
    """Retrieve full records (FASTA, GenBank flat file, XML, ...).

    Unlike the other eutils here, this never speaks JSON: retmode is a real
    choice of raw output format, not something NCBI can render as structured
    data for arbitrary rettypes.
    """
    if ids is None and (webenv is None or query_key is None):
        raise ValueError("efetch requires either ids or both webenv and query_key")

    params: dict = {"db": db, "rettype": rettype, "retmode": retmode}
    if ids is not None:
        params["id"] = _ids_to_str(ids)
    else:
        params["WebEnv"] = webenv
        params["query_key"] = query_key

    return _request_text(client, "efetch.fcgi", params)


def _parse_epost_response(body: str) -> dict:
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as e:
        raise NCBIAPIError(f"epost returned unparseable XML: {body!r}") from e

    webenv = root.findtext("WebEnv")
    query_key = root.findtext("QueryKey")
    if webenv is None or query_key is None:
        raise NCBIAPIError(f"epost response missing WebEnv/QueryKey: {body!r}")

    return {"webenv": webenv, "query_key": int(query_key)}


def epost(client: NCBIClient, db: str, ids) -> dict:
    """Upload an ID list to the history server. Returns {"webenv", "query_key"}.

    Sent via POST (NCBI's own recommendation for epost) since its purpose is
    uploading ID lists too large for a GET URL — epost only ever returns XML,
    never JSON, regardless of retmode.
    """
    body = _request_text(client, "epost.fcgi", {"db": db, "id": _ids_to_str(ids)}, method="POST")
    return _parse_epost_response(body)
