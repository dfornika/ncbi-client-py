import respx
from httpx import Response

from ncbi_client import datasets


def test_fetch_taxonomy(client):
    with respx.mock:
        respx.get("https://api.ncbi.nlm.nih.gov/datasets/v2/taxonomy/taxon/9606/dataset_report").mock(
            return_value=Response(200, json={
                "reports": [{"taxonomy": {"tax_id": 9606, "current_scientific_name": {"name": "Homo sapiens"}, "rank": "SPECIES"}}],
                "total_count": 1,
            })
        )
        page = datasets.fetch(client, "taxonomy-data-report", {"taxons": ["9606"]}, "taxonomy")

    assert len(page["results"]) == 1
    assert page["results"][0]["tax_id"] == 9606
    assert page["total_count"] == 1
    assert page["next_page_token"] is None


def test_fetch_one(client):
    with respx.mock:
        respx.get("https://api.ncbi.nlm.nih.gov/datasets/v2/gene/id/672").mock(
            return_value=Response(200, json={
                "reports": [{"gene": {"gene_id": "672", "symbol": "BRCA1"}}],
                "total_count": 1,
            })
        )
        result = datasets.fetch_one(client, "gene-reports-by-id", {"gene_ids": [672]}, "gene")

    assert result["gene_id"] == "672"
    assert result["symbol"] == "BRCA1"


def test_fetch_one_empty(client):
    with respx.mock:
        respx.get("https://api.ncbi.nlm.nih.gov/datasets/v2/gene/id/999999").mock(
            return_value=Response(200, json={"reports": [], "total_count": 0})
        )
        result = datasets.fetch_one(client, "gene-reports-by-id", {"gene_ids": [999999]}, "gene")

    assert result is None


def test_fetch_all_pagination(client):
    call_count = 0

    def side_effect(request):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return Response(200, json={
                "reports": [{"taxonomy": {"tax_id": 9606}}],
                "total_count": 2,
                "next_page_token": "page2",
            })
        return Response(200, json={
            "reports": [{"taxonomy": {"tax_id": 10090}}],
            "total_count": 2,
        })

    with respx.mock:
        respx.get("https://api.ncbi.nlm.nih.gov/datasets/v2/taxonomy/taxon/9606,10090/dataset_report").mock(
            side_effect=side_effect
        )
        results = list(datasets.fetch_all(client, "taxonomy-data-report", {"taxons": ["9606", "10090"]}, "taxonomy"))

    assert len(results) == 2
    assert results[0]["tax_id"] == 9606
    assert results[1]["tax_id"] == 10090
    assert call_count == 2


def test_fetch_assembly(client):
    with respx.mock:
        respx.get("https://api.ncbi.nlm.nih.gov/datasets/v2/genome/accession/GCF_000001405.40/dataset_report").mock(
            return_value=Response(200, json={
                "reports": [{"accession": "GCF_000001405.40", "organism": {"tax_id": 9606}}],
                "total_count": 1,
            })
        )
        result = datasets.fetch_one(client, "genome-dataset-report", {"accessions": ["GCF_000001405.40"]}, "assembly")

    assert result["accession"] == "GCF_000001405.40"


def test_report_extraction_identity(client):
    with respx.mock:
        respx.get("https://api.ncbi.nlm.nih.gov/datasets/v2/genome/accession/GCF_000001405.40/sequence_reports").mock(
            return_value=Response(200, json={
                "reports": [{"chr_name": "1", "length": 248956422}],
                "total_count": 1,
            })
        )
        page = datasets.fetch(client, "genome-sequence-report", {"accession": "GCF_000001405.40"}, "sequence")

    assert page["results"][0]["chr_name"] == "1"


def test_api_key_header(client):
    with respx.mock:
        route = respx.get("https://api.ncbi.nlm.nih.gov/datasets/v2/gene/id/672").mock(
            return_value=Response(200, json={"reports": [{"gene": {"gene_id": "672"}}], "total_count": 1})
        )
        datasets.fetch_one(client, "gene-reports-by-id", {"gene_ids": [672]}, "gene")

    assert route.calls[0].request.headers["api-token"] == "test-key"
