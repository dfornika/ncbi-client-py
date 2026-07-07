from unittest.mock import patch

import pytest

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


def test_lookup_dataset_entity_biosample(client):
    # Regression test: a BioSample's own esummary carries its accession under
    # "accession", not "biosampleaccn" (that field only appears on summaries
    # from other dbs, e.g. assembly, as a cross-reference back to BioSample).
    fake_biosample = {"accession": "SAMN02604091"}
    with patch.object(bridge.datasets, "fetch_one", return_value=fake_biosample) as mock_fetch:
        result = bridge.lookup_dataset_entity(
            client, "biosample", {"uid": "2604091", "accession": "SAMN02604091"}
        )

    assert result == fake_biosample
    mock_fetch.assert_called_once_with(
        client, "bio-sample-dataset-report", {"accessions": ["SAMN02604091"]}, "biosample"
    )


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


def test_biosample_assembly_accessions(client):
    fake_page = {
        "results": [{"accession": "GCA_000005845.2"}, {"accession": "GCF_000005845.2"}],
        "total_count": 2,
    }
    with patch.object(bridge.datasets, "fetch", return_value=fake_page) as mock_fetch:
        result = bridge.biosample_assembly_accessions(client, "SAMN02604091")

    assert result == ["GCA_000005845.2", "GCF_000005845.2"]
    mock_fetch.assert_called_once_with(
        client, "genome-dataset-reports-by-biosample-id", {"biosample_ids": ["SAMN02604091"]}, "assembly"
    )


def test_biosample_assembly_accessions_skips_missing_accession(client):
    fake_page = {"results": [{"accession": "GCF_000005845.2"}, {}], "total_count": 2}
    with patch.object(bridge.datasets, "fetch", return_value=fake_page):
        result = bridge.biosample_assembly_accessions(client, "SAMN02604091")

    assert result == ["GCF_000005845.2"]


def test_parse_sra_run_accessions_single():
    runs_xml = '<Run acc="SRR10971019" total_spots="95514"/>'
    assert bridge._parse_sra_run_accessions(runs_xml) == ["SRR10971019"]


def test_parse_sra_run_accessions_multiple():
    runs_xml = '<Run acc="SRR000001"/><Run acc="SRR000002"/>'
    assert bridge._parse_sra_run_accessions(runs_xml) == ["SRR000001", "SRR000002"]


def test_parse_sra_run_accessions_empty():
    assert bridge._parse_sra_run_accessions("") == []


def test_parse_sra_run_accessions_malformed_raises_clear_error():
    with pytest.raises(bridge.NCBIAPIError, match="unparseable"):
        bridge._parse_sra_run_accessions("<Run acc=\"SRR000001\"")


def test_biosample_sra_run_accessions(client):
    with patch.object(bridge.eutils, "esearch", return_value={"ids": ["2604091"], "count": 1}) as mock_esearch:
        with patch.object(
            bridge.eutils,
            "elink",
            return_value=[{"dbto": "sra", "linkname": "biosample_sra", "ids": ["13428597", "13428595"]}],
        ) as mock_elink:
            with patch.object(
                bridge.eutils,
                "esummary",
                return_value=[
                    {"uid": "13428597", "runs": '<Run acc="SRR13921543"/>'},
                    {"uid": "13428595", "runs": '<Run acc="SRR13921545"/>'},
                ],
            ) as mock_esummary:
                result = bridge.biosample_sra_run_accessions(client, "SAMN02604091")

    assert result == ["SRR13921543", "SRR13921545"]
    mock_esearch.assert_called_once_with(client, "biosample", "SAMN02604091[accn]")
    mock_elink.assert_called_once_with(client, "biosample", "2604091", linkname="biosample_sra")
    mock_esummary.assert_called_once_with(client, "sra", ["13428597", "13428595"])


def test_biosample_sra_run_accessions_no_biosample_found(client):
    with patch.object(bridge.eutils, "esearch", return_value={"ids": [], "count": 0}):
        result = bridge.biosample_sra_run_accessions(client, "SAMN00000000")

    assert result == []


def test_biosample_sra_run_accessions_no_sra_links(client):
    with patch.object(bridge.eutils, "esearch", return_value={"ids": ["2604091"], "count": 1}):
        with patch.object(bridge.eutils, "elink", return_value=[]):
            result = bridge.biosample_sra_run_accessions(client, "SAMN02604091")

    assert result == []


def test_biosample_sra_run_accessions_batches_large_id_lists(client):
    # A BioSample with more linked SRA records than _ESUMMARY_BATCH_SIZE must
    # not silently truncate: every id should still make it into some batch.
    sra_uids = [str(i) for i in range(250)]

    def fake_esummary(client, db, ids):
        return [{"uid": uid, "runs": f'<Run acc="SRR{uid}"/>'} for uid in ids]

    with patch.object(bridge.eutils, "esearch", return_value={"ids": ["2604091"], "count": 1}):
        with patch.object(bridge.eutils, "elink", return_value=[{"dbto": "sra", "ids": sra_uids}]):
            with patch.object(bridge.eutils, "esummary", side_effect=fake_esummary) as mock_esummary:
                result = bridge.biosample_sra_run_accessions(client, "SAMN02604091")

    assert mock_esummary.call_count == 2
    assert len(mock_esummary.call_args_list[0].args[2]) == 200
    assert len(mock_esummary.call_args_list[1].args[2]) == 50
    assert len(result) == 250
    assert result[0] == "SRR0"
    assert result[-1] == "SRR249"
