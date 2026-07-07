from __future__ import annotations

import os
import tempfile
from collections.abc import Generator
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from ncbi_client.throttle import with_retry

if TYPE_CHECKING:
    from ncbi_client.client import NCBIClient

DATASETS_BASE_URL = "https://api.ncbi.nlm.nih.gov/datasets/v2"

# Download endpoints can transfer multi-GB genome packages; give them a much
# longer read timeout than the client's default 30s, without changing that
# default for every other (small, JSON) request the client makes.
DOWNLOAD_TIMEOUT = httpx.Timeout(connect=30.0, read=300.0, write=30.0, pool=30.0)

OPERATIONS = {
    "taxonomy-data-report": {
        "method": "GET",
        "path": "/taxonomy/taxon/{taxons}/dataset_report",
        "path_params": ["taxons"],
    },
    "taxonomy-image-metadata": {
        "method": "GET",
        "path": "/taxonomy/taxon/{taxon}/image/metadata",
        "path_params": ["taxon"],
    },
    "taxonomy-links": {
        "method": "GET",
        "path": "/taxonomy/taxon/{taxon}/links",
        "path_params": ["taxon"],
    },
    "genome-dataset-report": {
        "method": "GET",
        "path": "/genome/accession/{accessions}/dataset_report",
        "path_params": ["accessions"],
    },
    "genome-dataset-reports-by-taxon": {
        "method": "GET",
        "path": "/genome/taxon/{taxons}/dataset_report",
        "path_params": ["taxons"],
    },
    "genome-dataset-reports-by-biosample-id": {
        "method": "GET",
        "path": "/genome/biosample/{biosample_ids}/dataset_report",
        "path_params": ["biosample_ids"],
    },
    "genome-annotation-report": {
        "method": "GET",
        "path": "/genome/accession/{accession}/annotation_report",
        "path_params": ["accession"],
    },
    "genome-sequence-report": {
        "method": "GET",
        "path": "/genome/accession/{accession}/sequence_reports",
        "path_params": ["accession"],
    },
    "genome-links-by-accession": {
        "method": "GET",
        "path": "/genome/accession/{accessions}/links",
        "path_params": ["accessions"],
    },
    "gene-reports-by-id": {
        "method": "GET",
        "path": "/gene/id/{gene_ids}",
        "path_params": ["gene_ids"],
    },
    "gene-orthologs-by-id": {
        "method": "GET",
        "path": "/gene/id/{gene_id}/orthologs",
        "path_params": ["gene_id"],
    },
    "gene-product-reports-by-id": {
        "method": "GET",
        "path": "/gene/id/{gene_ids}/product_report",
        "path_params": ["gene_ids"],
    },
    "gene-links-by-id": {
        "method": "GET",
        "path": "/gene/id/{gene_ids}/links",
        "path_params": ["gene_ids"],
    },
    "gene-dataset-reports-by-taxon": {
        "method": "GET",
        "path": "/gene/taxon/{taxon}/dataset_report",
        "path_params": ["taxon"],
    },
    "bio-sample-dataset-report": {
        "method": "GET",
        "path": "/biosample/accession/{accessions}/biosample_report",
        "path_params": ["accessions"],
    },
    "virus-reports-by-taxon": {
        "method": "GET",
        "path": "/virus/taxon/{taxon}/dataset_report",
        "path_params": ["taxon"],
    },
    "virus-reports-by-acessions": {
        "method": "GET",
        "path": "/virus/accession/{accessions}/dataset_report",
        "path_params": ["accessions"],
    },
    "virus-annotation-reports-by-taxon": {
        "method": "GET",
        "path": "/virus/taxon/{taxon}/annotation_report",
        "path_params": ["taxon"],
    },
    "virus-annotation-reports-by-acessions": {
        "method": "GET",
        "path": "/virus/accession/{accessions}/annotation_report",
        "path_params": ["accessions"],
    },
}

DOWNLOAD_OPERATIONS = {
    "genome-accession-download": {
        "method": "GET",
        "path": "/genome/accession/{accessions}/download",
        "path_params": ["accessions"],
    },
    "gene-id-download": {
        "method": "GET",
        "path": "/gene/id/{gene_ids}/download",
        "path_params": ["gene_ids"],
    },
}

REPORT_EXTRACTORS = {
    "taxonomy": "taxonomy",
    "gene": "gene",
    "gene-product": "product",
    "annotation": "annotation",
}


def _extract_report(entity_type: str, report: dict) -> dict:
    key = REPORT_EXTRACTORS.get(entity_type)
    if key:
        return report.get(key, report)
    return report


def _build_request(op: dict, params: dict) -> tuple[str, dict]:
    path = op["path"]
    query = {}
    remaining = dict(params)

    for pp in op.get("path_params", []):
        val = remaining.pop(pp, None)
        if val is not None:
            if isinstance(val, (list, tuple)):
                val = ",".join(str(v) for v in val)
            path = path.replace(f"{{{pp}}}", str(val))

    query = remaining
    url = DATASETS_BASE_URL + path
    return url, query


def _do_request(client: NCBIClient, method: str, url: str, query: dict) -> dict:
    headers = {}
    if client.api_key:
        headers["api-token"] = client.api_key

    resp = client.http.request(method, url, params=query, headers=headers)
    resp.raise_for_status()
    return resp.json()


def _do_download_request(client: NCBIClient, method: str, url: str, query: dict, destination: Path) -> None:
    headers = {}
    if client.api_key:
        headers["api-token"] = client.api_key

    # Stream to a sibling temp file and rename into place on success, so a
    # failed or interrupted download never leaves a corrupt file at
    # `destination`.
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(destination.parent), prefix=f".{destination.name}.", suffix=".part"
    )
    try:
        with os.fdopen(tmp_fd, "wb") as f:
            with client.http.stream(method, url, params=query, headers=headers, timeout=DOWNLOAD_TIMEOUT) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes():
                    f.write(chunk)
        os.replace(tmp_path, destination)
    except BaseException:
        Path(tmp_path).unlink(missing_ok=True)
        raise


def download(client: NCBIClient, operation: str, params: dict, destination: str | os.PathLike) -> Path:
    """Download a Datasets v2 zip package (genome/gene data package) to `destination`.

    Unlike fetch/fetch_one/fetch_all, this doesn't parse a JSON envelope: the
    response body is streamed directly to disk. A 429/5xx encountered before
    or while headers arrive is retried like any other request, but a
    connection drop mid-stream (after the body has started arriving) is not
    retried and is raised to the caller as-is — safely resuming a partial
    download would need Range-request support, which isn't implemented yet.
    """
    op = DOWNLOAD_OPERATIONS[operation]
    url, query = _build_request(op, params)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    client.rate_limiter.acquire()
    with_retry(client.rate_limiter, lambda: _do_download_request(client, op["method"], url, query, destination))
    return destination


def fetch(client: NCBIClient, operation: str, params: dict, entity_type: str) -> dict:
    op = OPERATIONS[operation]
    url, query = _build_request(op, params)

    client.rate_limiter.acquire()
    response = with_retry(client.rate_limiter, lambda: _do_request(client, op["method"], url, query))

    reports = response.get("reports", [])
    extracted = [_extract_report(entity_type, r) for r in reports]
    return {
        "results": extracted,
        "total_count": response.get("total_count"),
        "next_page_token": response.get("next_page_token"),
        "entity_type": entity_type,
    }


def fetch_one(client: NCBIClient, operation: str, params: dict, entity_type: str) -> dict | None:
    page = fetch(client, operation, params, entity_type)
    results = page["results"]
    return results[0] if results else None


def fetch_all(client: NCBIClient, operation: str, params: dict, entity_type: str) -> Generator[dict, None, None]:
    current_params = dict(params)
    while True:
        page = fetch(client, operation, current_params, entity_type)
        yield from page["results"]
        token = page.get("next_page_token")
        if not token:
            break
        current_params["page_token"] = token
