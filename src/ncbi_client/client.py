from __future__ import annotations

import os

import httpx

from ncbi_client import bridge, datasets, eutils
from ncbi_client.throttle import RateLimiter


class NCBIClient:
    def __init__(self, api_key: str | None = None, tool: str = "ncbi-client-py", email: str | None = None):
        self.api_key = api_key or os.environ.get("NCBI_API_KEY", "")
        self.tool = tool
        self.email = email
        self.rate_limiter = RateLimiter(10.0 if self.api_key else 3.0)
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

    # --- E-utilities ---

    def einfo(self, db=None):
        return eutils.einfo(self, db)

    def esearch(self, db, term, **opts):
        return eutils.esearch(self, db, term, **opts)

    def esummary(self, db, ids):
        return eutils.esummary(self, db, ids)

    def elink(self, dbfrom, ids, **opts):
        return eutils.elink(self, dbfrom, ids, **opts)

    def elink_available(self, dbfrom, ids):
        return eutils.elink_available(self, dbfrom, ids)

    # --- Bridge ---

    def search(self, db, term, **opts):
        return bridge.search(self, db, term, **opts)

    def lookup_dataset_entity(self, db, summary):
        return bridge.lookup_dataset_entity(self, db, summary)

    def follow_elink(self, db, uid, linkname):
        return bridge.follow_elink(self, db, uid, linkname)

    def discover_links(self, db, uid):
        return bridge.discover_links(self, db, uid)
