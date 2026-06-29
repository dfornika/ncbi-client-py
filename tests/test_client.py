from unittest.mock import patch

import respx
from httpx import Response

from ncbi_client import NCBIClient


def test_client_creates_with_defaults():
    client = NCBIClient()
    assert client.tool == "ncbi-client-py"
    assert client.rate_limiter._refill_rate == 3.0
    client.close()


def test_client_creates_with_api_key():
    client = NCBIClient(api_key="my-key")
    assert client.api_key == "my-key"
    assert client.rate_limiter._refill_rate == 10.0
    client.close()


def test_client_context_manager():
    with NCBIClient() as client:
        assert client.http is not None


def test_taxonomy_scalar(client):
    with respx.mock:
        respx.get("https://api.ncbi.nlm.nih.gov/datasets/v2/taxonomy/taxon/9606/dataset_report").mock(
            return_value=Response(200, json={
                "reports": [{"taxonomy": {"tax_id": 9606, "rank": "SPECIES"}}],
                "total_count": 1,
            })
        )
        result = client.taxonomy("9606")

    assert result["tax_id"] == 9606


def test_taxonomy_collection(client):
    with respx.mock:
        respx.get("https://api.ncbi.nlm.nih.gov/datasets/v2/taxonomy/taxon/9606/dataset_report").mock(
            return_value=Response(200, json={
                "reports": [{"taxonomy": {"tax_id": 9606}}],
                "total_count": 1,
            })
        )
        result = client.taxonomy(["9606"])

    assert isinstance(result, dict)
    assert len(result["results"]) == 1


def test_gene_scalar(client):
    with respx.mock:
        respx.get("https://api.ncbi.nlm.nih.gov/datasets/v2/gene/id/672").mock(
            return_value=Response(200, json={
                "reports": [{"gene": {"gene_id": "672", "symbol": "BRCA1"}}],
                "total_count": 1,
            })
        )
        result = client.gene(672)

    assert result["gene_id"] == "672"
    assert result["symbol"] == "BRCA1"


def test_assembly_scalar(client):
    with respx.mock:
        respx.get("https://api.ncbi.nlm.nih.gov/datasets/v2/genome/accession/GCF_000001405.40/dataset_report").mock(
            return_value=Response(200, json={
                "reports": [{"accession": "GCF_000001405.40"}],
                "total_count": 1,
            })
        )
        result = client.assembly("GCF_000001405.40")

    assert result["accession"] == "GCF_000001405.40"


def test_search_delegates_to_bridge(client):
    from ncbi_client import bridge
    fake_result = {"results": [], "total_count": 0, "retmax": 20, "retstart": 0, "db": "gene"}
    with patch.object(bridge, "search", return_value=fake_result) as mock_search:
        result = client.search("gene", "BRCA1")

    mock_search.assert_called_once_with(client, "gene", "BRCA1")
    assert result == fake_result


def test_einfo_delegates(client):
    with respx.mock:
        respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi").mock(
            return_value=Response(200, json={"einforesult": {"dblist": ["gene", "pubmed"]}})
        )
        result = client.einfo()

    assert result == ["gene", "pubmed"]
