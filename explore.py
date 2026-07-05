#!/bin/env python

"""
Scratchpad for exploring the NCBI client.

Usage:
    source .venv/bin/activate
    python explore.py

Set NCBI_API_KEY in your environment for higher rate limits (10/sec vs 3/sec).
"""

from pprint import pprint

from ncbi_client import NCBIClient


def section(title):
    print(f"\n{'=' * 60}")
    print(f"  {title}")
    print(f"{'=' * 60}\n")


client = NCBIClient()

# --- E-utilities: database discovery ---

section("Available Entrez databases")
dbs = client.einfo()
print(f"{len(dbs)} databases: {', '.join(dbs[:10])}...")

section("Gene database info")
gene_info = client.einfo("gene")
pprint({k: gene_info[k] for k in ["dbname", "count", "lastupdate", "description"]})

# --- Datasets: direct entity lookups ---

section("Taxonomy: Homo sapiens (taxon 9606)")
human = client.taxonomy("9606")
pprint({
    "tax_id": human["tax_id"],
    "name": human["current_scientific_name"]["name"],
    "rank": human["rank"],
    "children": len(human.get("children", [])),
    "parents": len(human.get("parents", [])),
})

section("Gene: BRCA1 (gene ID 672)")
brca1 = client.gene(672)
pprint({
    "gene_id": brca1["gene_id"],
    "symbol": brca1["symbol"],
    "description": brca1.get("description"),
    "taxname": brca1.get("taxname"),
    "type": brca1.get("type"),
    "chromosomes": brca1.get("chromosomes"),
})

section("Assembly: GRCh38 (GCF_000001405.40)")
grch38 = client.assembly("GCF_000001405.40")
pprint({
    "accession": grch38["accession"],
    "organism": grch38["organism"]["organism_name"],
    "level": grch38.get("assembly_info", {}).get("assembly_level"),
    "name": grch38.get("assembly_info", {}).get("assembly_name"),
})

# --- E-utilities: search + bridge ---

section("Search Entrez 'gene' for 'BRCA1 human'")
results = client.search("gene", "BRCA1 human", retmax=5)
print(f"Total hits: {results['total_count']}, showing {len(results['results'])}")
for r in results["results"]:
    print(f"  UID={r['uid']}  {r.get('name', '')}  — {r.get('description', '')[:60]}")

section("Bridge: first search result → Datasets gene entity")
if results["results"]:
    first = results["results"][0]
    entity = client.lookup_dataset_entity("gene", first)
    if entity:
        pprint({
            "gene_id": entity["gene_id"],
            "symbol": entity["symbol"],
            "description": entity.get("description"),
        })

section("Discover cross-database links for gene UID 672")
links = client.discover_links("gene", "672")
for link in links[:10]:
    print(f"  {link['linkname']:40s} → {link['dbto']}")
if len(links) > 10:
    print(f"  ... and {len(links) - 10} more")

section("Follow gene → PubMed link")
pubmed = client.follow_elink("gene", "672", "gene_pubmed")
print(f"Total linked PubMed IDs: {pubmed['total_count']}, fetched {len(pubmed['results'])}")
for r in pubmed["results"][:3]:
    print(f"  PMID={r.get('uid')}  {r.get('title', '')[:70]}")

# --- Datasets: pagination ---

section("Fetch all assemblies for E. coli (taxon 562) — first 5")
ecoli_assemblies = client.fetch_all(
    "genome-dataset-reports-by-taxon", {"taxons": ["562"]}, "assembly"
)
for i, asm in enumerate(ecoli_assemblies):
    if i >= 5:
        print(f"  ... (stopping after 5)")
        break
    print(f"  {asm['accession']:25s}  {asm.get('assembly_info', {}).get('assembly_name', '')}")

# --- Viruses ---

section("Virus genomes for SARS-CoV-2 (taxon 2697049) — first 3")
viruses = client.virus("2697049")
for v in viruses[:3]:
    pprint({
        "accession": v.get("accession"),
        "virus": v.get("virus", {}).get("organism_name"),
        "host": v.get("host", {}).get("organism_name"),
        "length": v.get("length"),
    })

print("\n✓ Exploration complete.")
