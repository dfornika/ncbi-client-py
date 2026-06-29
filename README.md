# ncbi-client

Python client for the [NCBI Datasets API](https://www.ncbi.nlm.nih.gov/datasets/) and [E-utilities](https://www.ncbi.nlm.nih.gov/books/NBK25501/), providing a unified interface to search, fetch, and link biological entities across NCBI's databases.

## Install

```bash
pip install -e .
```

## Quick start

```python
from ncbi_client import NCBIClient

client = NCBIClient()  # reads NCBI_API_KEY from env if set

# Fetch a gene by ID
brca1 = client.gene(672)
print(brca1["symbol"], brca1["description"])
# BRCA1 BRCA1 DNA repair associated

# Search Entrez and bridge to Datasets
results = client.search("gene", "BRCA1 human", retmax=3)
entity = client.lookup_dataset_entity("gene", results["results"][0])
```

Set `NCBI_API_KEY` in your environment for 10 requests/sec (vs 3/sec without).

## API reference

### NCBIClient

```python
NCBIClient(api_key=None, tool="ncbi-client-py", email=None)
```

The client is a context manager:

```python
with NCBIClient(api_key="...") as client:
    human = client.taxonomy("9606")
```

### Datasets API methods

These fetch structured entity data from the NCBI Datasets API (v2). Methods that accept a single ID return a dict; pass a list to get a page dict with `results`, `total_count`, and `next_page_token`.

| Method | Arguments | Returns |
|--------|-----------|---------|
| `taxonomy(taxon_ids)` | Taxon ID string or list | Taxonomy record(s) |
| `assembly(accessions)` | Accession string or list | Genome assembly record(s) |
| `gene(gene_ids)` | Integer gene ID or list | Gene record(s) |
| `biosample(accessions)` | Accession string or list | Biosample record(s) |
| `sequences(accession)` | Assembly accession string | List of sequence records |
| `gene_products(gene_ids)` | Integer gene ID or list | Gene product record(s) |
| `annotations(accession)` | Assembly accession string | List of annotation records |
| `virus(taxon)` | Taxon ID string | List of virus records |
| `virus_by_accession(accessions)` | Accession string or list | Virus record(s) |
| `virus_annotations(taxon)` | Taxon ID string | List of virus annotation records |
| `virus_annotations_by_accession(accessions)` | Accession string or list | Virus annotation record(s) |
| `fetch_all(operation, params, entity_type)` | Operation name, params dict, entity type | Generator over all pages |

#### Scalar vs. collection

```python
# Single ID → dict (the entity)
gene = client.gene(672)
gene["symbol"]  # "BRCA1"

# List of IDs → page dict
page = client.gene([672, 675])
page["results"]      # [{"gene_id": "672", ...}, {"gene_id": "675", ...}]
page["total_count"]  # 2
```

#### Pagination

```python
# Auto-paginate through all E. coli assemblies
for assembly in client.fetch_all("genome-dataset-reports-by-taxon", {"taxons": ["562"]}, "assembly"):
    print(assembly["accession"])
```

### E-utilities methods

These query NCBI's Entrez system directly. All return parsed JSON as dicts/lists.

| Method | Description |
|--------|-------------|
| `einfo()` | List all 39 Entrez database names |
| `einfo(db)` | Get metadata for a specific database (fields, links, record count) |
| `esearch(db, term, **opts)` | Keyword search → `{"ids": [...], "count": int, "retmax": int, "retstart": int}` |
| `esummary(db, ids)` | Fetch document summaries for a list of UIDs → `list[dict]` |
| `elink(dbfrom, ids, **opts)` | Find linked UIDs across databases → `list[{"dbto", "linkname", "ids"}]` |
| `elink_available(dbfrom, ids)` | List available link types → `list[{"linkname", "dbto", "menutag"}]` |

```python
# Search PubMed
results = client.esearch("pubmed", "CRISPR cas9 review", retmax=5)
summaries = client.esummary("pubmed", results["ids"])
for s in summaries:
    print(s["uid"], s.get("title", "")[:80])
```

### Bridge methods

The bridge connects E-utilities search results to Datasets entities — search by keyword, then fetch structured records.

| Method | Description |
|--------|-------------|
| `search(db, term, **opts)` | esearch + esummary in one call → `{"results": [...], "total_count": int, "db": str}` |
| `lookup_dataset_entity(db, summary)` | Given a search result summary, fetch the Datasets entity (gene, taxonomy, assembly, or biosample). Returns `None` if no mapping exists for that database. |
| `follow_elink(db, uid, linkname)` | Follow a cross-database link → `{"results": [...], "total_count": int, "linkname": str, "dbto": str}` |
| `discover_links(db, uid)` | List available cross-database links for a UID → `list[{"linkname", "dbto", "menutag"}]` |

#### Bridged databases

These Entrez databases have mappings to Datasets entities:

| Entrez DB | Datasets entity | ID handling |
|-----------|----------------|-------------|
| `gene` | Gene | UID parsed as integer |
| `taxonomy` | Taxonomy | UID as string |
| `assembly` | Assembly | Accession from esummary `assemblyaccession` field |
| `biosample` | Biosample | Accession from esummary `biosampleaccn` field |

Other databases (pubmed, nuccore, protein, etc.) work with `search()`, `esummary()`, and `elink()` but don't bridge into Datasets.

#### Example: search → bridge → link

```python
# Search for a gene
results = client.search("gene", "TP53 human", retmax=1)
summary = results["results"][0]

# Bridge to Datasets entity
gene = client.lookup_dataset_entity("gene", summary)

# Discover and follow cross-database links
links = client.discover_links("gene", summary["uid"])
pubmed = client.follow_elink("gene", summary["uid"], "gene_pubmed")
print(f"{pubmed['total_count']} linked PubMed articles")
```

## Rate limiting

The client includes a token-bucket rate limiter shared across all requests:
- **Without API key**: 3 requests/sec
- **With API key**: 10 requests/sec

Retryable errors (HTTP 429, 5xx) are automatically retried up to 3 times with exponential backoff.

## Development

```bash
pip install -e ".[dev]"
pytest tests/ -v
```
