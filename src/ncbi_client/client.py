from __future__ import annotations

import os
from pathlib import Path

import httpx

from ncbi_client import blast, bridge, datasets, eutils, sra, submission
from ncbi_client.throttle import MinIntervalLimiter, RateLimiter


class NCBIClient:
    def __init__(self, api_key: str | None = None, tool: str = "ncbi-client-py", email: str | None = None):
        self.api_key = api_key or os.environ.get("NCBI_API_KEY", "")
        self.tool = tool
        self.email = email
        self.rate_limiter = RateLimiter(10.0 if self.api_key else 3.0)
        self.blast_rate_limiter = MinIntervalLimiter(10.0)
        self.http = httpx.Client(timeout=30.0)

    def close(self):
        self.http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    # --- Datasets convenience methods ---

    def taxonomy(self, taxon_ids):
        if isinstance(taxon_ids, (list, tuple)):
            return datasets.fetch(self, "taxonomy-data-report", {"taxons": list(taxon_ids)}, "taxonomy")
        return datasets.fetch_one(self, "taxonomy-data-report", {"taxons": [taxon_ids]}, "taxonomy")

    def assembly(self, accessions):
        if isinstance(accessions, (list, tuple)):
            return datasets.fetch(self, "genome-dataset-report", {"accessions": list(accessions)}, "assembly")
        return datasets.fetch_one(self, "genome-dataset-report", {"accessions": [accessions]}, "assembly")

    def gene(self, gene_ids):
        if isinstance(gene_ids, (list, tuple)):
            ids = [int(g) for g in gene_ids]
            return datasets.fetch(self, "gene-reports-by-id", {"gene_ids": ids}, "gene")
        return datasets.fetch_one(self, "gene-reports-by-id", {"gene_ids": [int(gene_ids)]}, "gene")

    def biosample(self, accessions):
        if isinstance(accessions, (list, tuple)):
            return datasets.fetch(self, "bio-sample-dataset-report", {"accessions": list(accessions)}, "biosample")
        return datasets.fetch_one(self, "bio-sample-dataset-report", {"accessions": [accessions]}, "biosample")

    def sequences(self, assembly_accession: str):
        page = datasets.fetch(self, "genome-sequence-report", {"accession": assembly_accession}, "sequence")
        return page["results"]

    def gene_products(self, gene_ids):
        if isinstance(gene_ids, (list, tuple)):
            ids = [int(g) for g in gene_ids]
            return datasets.fetch(self, "gene-product-reports-by-id", {"gene_ids": ids}, "gene-product")
        return datasets.fetch_one(self, "gene-product-reports-by-id", {"gene_ids": [int(gene_ids)]}, "gene-product")

    def annotations(self, assembly_accession: str):
        page = datasets.fetch(self, "genome-annotation-report", {"accession": assembly_accession}, "annotation")
        return page["results"]

    def virus(self, taxon):
        page = datasets.fetch(self, "virus-reports-by-taxon", {"taxon": str(taxon)}, "virus")
        return page["results"]

    def virus_by_accession(self, accessions):
        if isinstance(accessions, (list, tuple)):
            return datasets.fetch(self, "virus-reports-by-acessions", {"accessions": list(accessions)}, "virus")
        return datasets.fetch_one(self, "virus-reports-by-acessions", {"accessions": [accessions]}, "virus")

    def virus_annotations(self, taxon):
        page = datasets.fetch(
            self, "virus-annotation-reports-by-taxon", {"taxon": str(taxon)}, "virus-annotation"
        )
        return page["results"]

    def virus_annotations_by_accession(self, accessions):
        if isinstance(accessions, (list, tuple)):
            return datasets.fetch(
                self, "virus-annotation-reports-by-acessions", {"accessions": list(accessions)}, "virus-annotation"
            )
        return datasets.fetch_one(
            self, "virus-annotation-reports-by-acessions", {"accessions": [accessions]}, "virus-annotation"
        )

    def fetch_all(self, operation: str, params: dict, entity_type: str):
        return datasets.fetch_all(self, operation, params, entity_type)

    def download_genome(self, accessions, destination, **opts):
        accessions = list(accessions) if isinstance(accessions, (list, tuple)) else [accessions]
        return datasets.download(self, "genome-accession-download", {"accessions": accessions, **opts}, destination)

    def download_gene(self, gene_ids, destination, **opts):
        gene_ids = [int(g) for g in gene_ids] if isinstance(gene_ids, (list, tuple)) else [int(gene_ids)]
        return datasets.download(self, "gene-id-download", {"gene_ids": gene_ids, **opts}, destination)

    # --- E-utilities ---

    def einfo(self, db=None):
        return eutils.einfo(self, db)

    def esearch(self, db, term, **opts):
        return eutils.esearch(self, db, term, **opts)

    def esummary(self, db, ids=None, **opts):
        return eutils.esummary(self, db, ids, **opts)

    def elink(self, dbfrom, ids=None, **opts):
        return eutils.elink(self, dbfrom, ids, **opts)

    def elink_available(self, dbfrom, ids):
        return eutils.elink_available(self, dbfrom, ids)

    def efetch(self, db, ids=None, **opts):
        return eutils.efetch(self, db, ids, **opts)

    def epost(self, db, ids):
        return eutils.epost(self, db, ids)

    # --- Bridge ---

    def search(self, db, term, **opts):
        return bridge.search(self, db, term, **opts)

    def lookup_dataset_entity(self, db, summary):
        return bridge.lookup_dataset_entity(self, db, summary)

    def follow_elink(self, db, uid, linkname):
        return bridge.follow_elink(self, db, uid, linkname)

    def discover_links(self, db, uid):
        return bridge.discover_links(self, db, uid)

    def biosample_assembly_accessions(self, biosample_accession: str) -> list[str]:
        return bridge.biosample_assembly_accessions(self, biosample_accession)

    def biosample_sra_run_accessions(self, biosample_accession: str) -> list[str]:
        return bridge.biosample_sra_run_accessions(self, biosample_accession)

    def download_biosample_assemblies(self, biosample_accession: str, destination_dir, **opts) -> list[Path]:
        """Download the genome package(s) for every assembly linked to a BioSample."""
        destination_dir = Path(destination_dir)
        accessions = bridge.biosample_assembly_accessions(self, biosample_accession)
        return [
            datasets.download(self, "genome-accession-download", {"accessions": [acc], **opts}, destination_dir / f"{acc}.zip")
            for acc in accessions
        ]

    def download_biosample_fastqs(self, biosample_accession: str, destination_dir) -> dict[str, list[Path]]:
        """Download FASTQ files (via ENA) for every SRA run linked to a BioSample."""
        run_accessions = bridge.biosample_sra_run_accessions(self, biosample_accession)
        return {run: sra.download_fastq(self, run, destination_dir) for run in run_accessions}

    # --- SRA ---

    def download_sra(self, accession: str, destination):
        return sra.download_sra(self, accession, destination)

    def copy_sra_to_s3(self, accession: str, dest_bucket: str, dest_key: str | None = None, *, s3_client=None, **copy_kwargs):
        return sra.copy_sra_to_s3(accession, dest_bucket, dest_key, s3_client=s3_client, **copy_kwargs)

    def download_fastq(self, accession: str, destination_dir):
        return sra.download_fastq(self, accession, destination_dir)

    # --- Submission ---

    def submit_biosamples(self, biosamples, organization, *, host, remote_base_path, **kwargs):
        return submission.submit_biosamples(biosamples, organization, host=host, remote_base_path=remote_base_path, **kwargs)

    def poll_biosample_submission(self, *, host, remote_folder, **kwargs):
        return submission.poll_submission_report(host=host, remote_folder=remote_folder, **kwargs)

    def submit_biosamples_and_wait(self, biosamples, organization, *, host, remote_base_path, **kwargs):
        return submission.submit_and_wait(biosamples, organization, host=host, remote_base_path=remote_base_path, **kwargs)

    # --- BLAST ---

    def blast(self, sequence, *, program, database, poll_interval=blast.MIN_POLL_INTERVAL, timeout=None, **opts):
        return blast.search(
            self, sequence, program=program, database=database,
            poll_interval=poll_interval, timeout=timeout, **opts,
        )

    def blast_submit(self, sequence, *, program, database, **opts):
        return blast.submit(self, sequence, program=program, database=database, **opts)

    def blast_status(self, rid):
        return blast.status(self, rid)

    def blast_fetch(self, rid, *, format_type="JSON2_S"):
        return blast.fetch(self, rid, format_type=format_type)
