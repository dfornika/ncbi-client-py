from __future__ import annotations

from typing import TYPE_CHECKING

from ncbi_client import datasets, eutils

if TYPE_CHECKING:
    from ncbi_client.client import NCBIClient

DB_TO_DATASETS = {
    "gene": {
        "entity_type": "gene",
        "operation": "gene-reports-by-id",
        "id_key": "gene_ids",
        "parse_id": int,
    },
    "taxonomy": {
        "entity_type": "taxonomy",
        "operation": "taxonomy-data-report",
        "id_key": "taxons",
        "parse_id": str,
    },
    "assembly": {
        "entity_type": "assembly",
        "operation": "genome-dataset-report",
        "id_key": "accessions",
        "id_from": "assemblyaccession",
    },
    "biosample": {
        "entity_type": "biosample",
        "operation": "bio-sample-dataset-report",
        "id_key": "accessions",
        "id_from": "biosampleaccn",
    },
}

_ELINK_ID_CAP = 200


def search(client: NCBIClient, db: str, term: str, **opts) -> dict:
    search_result = eutils.esearch(client, db, term, **opts)
    ids = search_result["ids"]
    summaries = eutils.esummary(client, db, ids) if ids else []
    return {
        "results": summaries,
        "total_count": search_result["count"],
        "retmax": search_result["retmax"],
        "retstart": search_result["retstart"],
        "db": db,
    }


def lookup_dataset_entity(client: NCBIClient, db: str, summary: dict) -> dict | None:
    mapping = DB_TO_DATASETS.get(db)
    if not mapping:
        return None

    uid = summary.get("uid")
    if "id_from" in mapping:
        lookup_id = summary.get(mapping["id_from"])
    else:
        parse_fn = mapping.get("parse_id", str)
        lookup_id = parse_fn(uid)

    if not lookup_id:
        return None

    return datasets.fetch_one(
        client, mapping["operation"], {mapping["id_key"]: [lookup_id]}, mapping["entity_type"]
    )


def follow_elink(client: NCBIClient, db: str, uid: str, linkname: str) -> dict:
    link_results = eutils.elink(client, db, uid, linkname=linkname)
    if not link_results:
        return {"results": [], "total_count": 0, "linkname": linkname, "dbto": None}

    first = link_results[0]
    all_ids = first["ids"]
    dbto = first["dbto"]
    total = len(all_ids)
    capped_ids = all_ids[:_ELINK_ID_CAP]

    mapping = DB_TO_DATASETS.get(dbto)
    if mapping and "parse_id" in mapping:
        parse_fn = mapping["parse_id"]
        page = datasets.fetch(
            client, mapping["operation"],
            {mapping["id_key"]: [parse_fn(i) for i in capped_ids]},
            mapping["entity_type"],
        )
        results = page["results"]
    else:
        results = eutils.esummary(client, dbto, capped_ids) if capped_ids else []

    return {"results": results, "total_count": total, "linkname": linkname, "dbto": dbto}


def discover_links(client: NCBIClient, db: str, uid: str) -> list[dict]:
    return eutils.elink_available(client, db, uid)
