from __future__ import annotations

from typing import TYPE_CHECKING
from xml.etree import ElementTree

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
        "id_from": "accession",
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


def biosample_assembly_accessions(client: NCBIClient, biosample_accession: str) -> list[str]:
    """Resolve a BioSample accession to its linked assembly accession(s) (GenBank and/or RefSeq)."""
    page = datasets.fetch(
        client, "genome-dataset-reports-by-biosample-id", {"biosample_ids": [biosample_accession]}, "assembly"
    )
    return [r["accession"] for r in page["results"] if r.get("accession")]


def _parse_sra_run_accessions(runs_xml: str) -> list[str]:
    # esummary's db="sra" DocSum embeds a fragment of sibling <Run/> elements
    # (not a single root) as an XML string inside a JSON field, rather than
    # returning structured JSON - wrap it so ElementTree can parse it.
    root = ElementTree.fromstring(f"<root>{runs_xml}</root>")
    return [acc for el in root.findall("Run") if (acc := el.get("acc"))]


def biosample_sra_run_accessions(client: NCBIClient, biosample_accession: str) -> list[str]:
    """Resolve a BioSample accession to its linked SRA run accession(s) (SRR/ERR/DRR).

    BioSample's own UID (used by elink) isn't the accession, so this first
    esearches for the UID, then follows the biosample_sra elink, then parses
    run accessions out of each linked SRA UID's esummary DocSum.
    """
    search_result = eutils.esearch(client, "biosample", f"{biosample_accession}[accn]")
    if not search_result["ids"]:
        return []

    uid = search_result["ids"][0]
    links = eutils.elink(client, "biosample", uid, linkname="biosample_sra")
    if not links:
        return []

    sra_uids = links[0]["ids"]
    if not sra_uids:
        return []

    summaries = eutils.esummary(client, "sra", sra_uids)
    run_accessions = []
    for summary in summaries:
        run_accessions.extend(_parse_sra_run_accessions(summary.get("runs") or ""))
    return run_accessions
