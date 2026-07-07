from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from ncbi_client._download import DOWNLOAD_TIMEOUT, stream_to_path
from ncbi_client.throttle import NCBIAPIError, with_retry

if TYPE_CHECKING:
    from ncbi_client.client import NCBIClient

try:
    import boto3
except ImportError:
    boto3 = None

SRA_ODP_BUCKET = "sra-pub-run-odp"
SRA_ODP_BASE_URL = f"https://{SRA_ODP_BUCKET}.s3.amazonaws.com"
SDL_BASE_URL = "https://locate.ncbi.nlm.nih.gov/sdl/2/retrieve"
ENA_FILEREPORT_URL = "https://www.ebi.ac.uk/ena/portal/api/filereport"

# Not every run is mirrored to the ODP bucket (dbGaP-protected, cold-storage,
# or lag-affected runs can be missing); these statuses both mean "not here" -
# a public GetObject-only bucket policy returns 403, not 404, for a missing
# key when the caller lacks ListBucket.
_ODP_NOT_FOUND_STATUSES = (403, 404)


class SRAError(NCBIAPIError):
    pass


def _sra_odp_key(accession: str) -> str:
    return f"sra/{accession}/{accession}"


def _do_http_download(client: NCBIClient, url: str, destination: Path) -> None:
    stream_to_path(destination, lambda: client.http.stream("GET", url, timeout=DOWNLOAD_TIMEOUT))


def _sdl_pick_location(sdl_response: dict, accession: str) -> dict:
    for result in sdl_response.get("result", []):
        if result.get("status") != 200:
            continue
        for file in result.get("files", []):
            if file.get("type") != "sra":
                continue
            # A plain httpx GET can't satisfy requester-pays billing, so skip
            # payRequired locations. Prefer NCBI's own HTTPS delivery
            # ("sra-ncbi") over an S3-backed link, since an "s3" location here
            # could be presigned/expiring; GCS ("gs") locations are never
            # considered (out of scope for this cut).
            locations = [loc for loc in file.get("locations", []) if loc.get("link") and not loc.get("payRequired")]
            for service in ("sra-ncbi", "s3"):
                for loc in locations:
                    if loc.get("service") == service:
                        return loc
    raise SRAError(f"SDL resolver returned no usable download location for {accession!r}")


def _fetch_sdl_location(client: NCBIClient, accession: str, filetype: str = "sra") -> dict:
    def do_request():
        resp = client.http.get(SDL_BASE_URL, params={"acc": accession, "filetype": filetype})
        resp.raise_for_status()
        return resp

    resp = with_retry(None, do_request)
    return _sdl_pick_location(resp.json(), accession)


def download_sra(client: NCBIClient, accession: str, destination: str | os.PathLike) -> Path:
    """Download the raw .sra object for `accession` to `destination`.

    Tries NCBI's public S3 Open Data bucket (`sra-pub-run-odp`) via a plain
    HTTPS GET first (object key `sra/{accession}/{accession}`); if that
    doesn't have the object (403 or 404), falls back to resolving a download
    link via the SDL API (https://locate.ncbi.nlm.nih.gov/sdl/2/retrieve).
    Raises SRAError if neither source has the accession.
    """
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)

    odp_url = f"{SRA_ODP_BASE_URL}/{_sra_odp_key(accession)}"
    try:
        with_retry(None, lambda: _do_http_download(client, odp_url, destination))
        return destination
    except httpx.HTTPStatusError as e:
        if e.response.status_code not in _ODP_NOT_FOUND_STATUSES:
            raise

    location = _fetch_sdl_location(client, accession)
    with_retry(None, lambda: _do_http_download(client, location["link"], destination))
    return destination


def copy_sra_to_s3(
    accession: str,
    dest_bucket: str,
    dest_key: str | None = None,
    *,
    s3_client=None,
    **copy_kwargs,
) -> str:
    """Server-side copy of the raw .sra object for `accession` from NCBI's
    public Open Data bucket (`sra-pub-run-odp`, us-east-1) directly into
    `dest_bucket`, without routing bytes through the local machine.

    Uses boto3's high-level `S3.Client.copy()` (not the low-level
    `copy_object()`), which transparently uses multipart copy for objects
    over 5GB - required since .sra files can exceed that. Requires the
    caller's own authenticated AWS credentials via the standard boto3
    credential chain; pass a pre-configured `s3_client` to control
    credentials/region/profile explicitly, otherwise one is constructed via
    `boto3.client("s3")`. `dest_bucket` should be in us-east-1 to avoid
    cross-region transfer charges (not enforced here). AWS only - GCS
    destinations aren't supported.
    """
    if s3_client is None:
        if boto3 is None:
            raise SRAError(
                "copy_sra_to_s3() requires boto3, which is not installed. "
                "Install it with: pip install ncbi-client[s3] "
                "(or pass a pre-configured client via s3_client=...)"
            )
        s3_client = boto3.client("s3")

    dest_key = dest_key or accession
    s3_client.copy(
        CopySource={"Bucket": SRA_ODP_BUCKET, "Key": _sra_odp_key(accession)},
        Bucket=dest_bucket,
        Key=dest_key,
        **copy_kwargs,
    )
    return f"s3://{dest_bucket}/{dest_key}"


def _fetch_ena_filereport(client: NCBIClient, accession: str) -> dict:
    params = {
        "accession": accession,
        "result": "read_run",
        "fields": "fastq_ftp,fastq_md5,fastq_bytes",
        "format": "json",
    }

    def do_request():
        resp = client.http.get(ENA_FILEREPORT_URL, params=params)
        resp.raise_for_status()
        return resp

    records = with_retry(None, do_request).json()
    if not records:
        raise SRAError(
            f"ENA Portal API returned no results for {accession!r}. This may be a transient "
            f"sync-lag gap for a very recent submission, or the accession doesn't exist / "
            f"isn't an SRA/ENA run accession."
        )
    return records[0]


def download_fastq(client: NCBIClient, accession: str, destination_dir: str | os.PathLike) -> list[Path]:
    """Download FASTQ file(s) for `accession` directly from EBI/ENA's mirror.

    Opt-in convenience path (depends on ENA, not NCBI). Paired-end runs
    produce 2 files (`_1`/`_2`), single-end runs produce 1. Returns the
    downloaded paths in ENA's file order.
    """
    record = _fetch_ena_filereport(client, accession)
    fastq_ftp = record.get("fastq_ftp") or ""
    if not fastq_ftp:
        raise SRAError(
            f"ENA has no FASTQ files for {accession!r} (fastq_ftp is empty). This run's "
            f"submission type (e.g. 10x/cellranger, PacBio/Nanopore native format, Complete "
            f"Genomics native) may not be auto-converted to FASTQ by ENA. Consider "
            f"download_sra() plus a local converter (e.g. fasterq-dump) instead."
        )

    destination_dir = Path(destination_dir)
    destination_dir.mkdir(parents=True, exist_ok=True)

    downloaded = []
    for ftp_path in fastq_ftp.split(";"):
        ftp_path = ftp_path.strip()
        if not ftp_path:
            continue
        # fastq_ftp values have no scheme prefix; https works on the same
        # host ENA's docs advertise for the ftp:// -> http:// substitution.
        url = f"https://{ftp_path}"
        dest = destination_dir / url.rsplit("/", 1)[-1]
        with_retry(None, lambda u=url, d=dest: _do_http_download(client, u, d))
        downloaded.append(dest)

    if not downloaded:
        raise SRAError(f"ENA's fastq_ftp field for {accession!r} contained no usable file paths: {fastq_ftp!r}")
    return downloaded
