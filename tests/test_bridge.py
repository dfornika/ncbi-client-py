from unittest.mock import patch

from ncbi_client import bridge


def test_search_returns_results(client):
    with patch.object(bridge.eutils, "esearch", return_value={"ids": ["672"], "count": 1, "retmax": 20, "retstart": 0}):
        with patch.object(bridge.eutils, "esummary", return_value=[{"uid": "672", "name": "BRCA1"}]):
            result = bridge.search(client, "gene", "BRCA1 human")

    assert result["total_count"] == 1
    assert result["db"] == "gene"
    assert len(result["results"]) == 1
    assert result["results"][0]["uid"] == "672"


def test_search_empty(client):
    with patch.object(bridge.eutils, "esearch", return_value={"ids": [], "count": 0, "retmax": 20, "retstart": 0}):
        result = bridge.search(client, "gene", "nonexistent_xyz")

    assert result["total_count"] == 0
    assert result["results"] == []


def test_lookup_dataset_entity_gene(client):
    fake_gene = {"gene_id": "672", "symbol": "BRCA1"}
    with patch.object(bridge.datasets, "fetch_one", return_value=fake_gene) as mock_fetch:
        result = bridge.lookup_dataset_entity(client, "gene", {"uid": "672"})

    assert result == fake_gene
    mock_fetch.assert_called_once_with(client, "gene-reports-by-id", {"gene_ids": [672]}, "gene")


def test_lookup_dataset_entity_assembly(client):
    fake_assembly = {"accession": "GCF_000001405.40"}
    with patch.object(bridge.datasets, "fetch_one", return_value=fake_assembly) as mock_fetch:
        result = bridge.lookup_dataset_entity(
            client, "assembly", {"uid": "123", "assemblyaccession": "GCF_000001405.40"}
        )

    assert result == fake_assembly
    mock_fetch.assert_called_once_with(
        client, "genome-dataset-report", {"accessions": ["GCF_000001405.40"]}, "assembly"
    )


def test_lookup_dataset_entity_no_mapping(client):
    result = bridge.lookup_dataset_entity(client, "pubmed", {"uid": "12345"})
    assert result is None


def test_follow_elink_to_datasets(client):
    fake_page = {"results": [{"gene_id": "672"}], "total_count": 1}
    with patch.object(bridge.eutils, "elink", return_value=[{"dbto": "gene", "linkname": "x_gene", "ids": ["672"]}]):
        with patch.object(bridge.datasets, "fetch", return_value=fake_page):
            result = bridge.follow_elink(client, "pubmed", "111", "x_gene")

    assert result["total_count"] == 1
    assert result["linkname"] == "x_gene"
    assert result["dbto"] == "gene"
    assert result["results"] == [{"gene_id": "672"}]


def test_follow_elink_to_esummary(client):
    with patch.object(bridge.eutils, "elink", return_value=[{"dbto": "pubmed", "linkname": "gene_pubmed", "ids": ["111", "222"]}]):
        with patch.object(bridge.eutils, "esummary", return_value=[{"uid": "111"}, {"uid": "222"}]):
            result = bridge.follow_elink(client, "gene", "672", "gene_pubmed")

    assert result["total_count"] == 2
    assert len(result["results"]) == 2


def test_follow_elink_empty(client):
    with patch.object(bridge.eutils, "elink", return_value=[]):
        result = bridge.follow_elink(client, "gene", "672", "gene_pubmed")

    assert result["results"] == []
    assert result["total_count"] == 0


def test_discover_links(client):
    fake_links = [{"linkname": "gene_pubmed", "dbto": "pubmed", "menutag": "PubMed"}]
    with patch.object(bridge.eutils, "elink_available", return_value=fake_links):
        result = bridge.discover_links(client, "gene", "672")

    assert len(result) == 1
    assert result[0]["linkname"] == "gene_pubmed"
