import respx
from httpx import Response

from ncbi_client import eutils


def test_einfo_list_databases(client):
    with respx.mock:
        respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi").mock(
            return_value=Response(200, json={"einforesult": {"dblist": ["gene", "pubmed", "taxonomy"]}})
        )
        result = eutils.einfo(client)
    assert result == ["gene", "pubmed", "taxonomy"]


def test_einfo_database_details(client):
    with respx.mock:
        respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi").mock(
            return_value=Response(200, json={
                "einforesult": {"dbinfo": [{"dbname": "gene", "count": "1000000"}]}
            })
        )
        result = eutils.einfo(client, "gene")
    assert result["dbname"] == "gene"
    assert result["count"] == "1000000"


def test_esearch(client):
    with respx.mock:
        respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
            return_value=Response(200, json={
                "esearchresult": {"idlist": ["672", "675"], "count": "2", "retmax": "20", "retstart": "0"}
            })
        )
        result = eutils.esearch(client, "gene", "BRCA1")
    assert result["ids"] == ["672", "675"]
    assert result["count"] == 2
    assert result["retmax"] == 20


def test_esummary(client):
    with respx.mock:
        respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi").mock(
            return_value=Response(200, json={
                "result": {"uids": ["672"], "672": {"uid": "672", "name": "BRCA1"}}
            })
        )
        result = eutils.esummary(client, "gene", ["672"])
    assert len(result) == 1
    assert result[0]["uid"] == "672"
    assert result[0]["name"] == "BRCA1"


def test_esummary_empty(client):
    with respx.mock:
        respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi").mock(
            return_value=Response(200, json={"result": {}})
        )
        result = eutils.esummary(client, "gene", ["999"])
    assert result == []


def test_elink(client):
    with respx.mock:
        respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi").mock(
            return_value=Response(200, json={
                "linksets": [{"linksetdbs": [{"dbto": "pubmed", "linkname": "gene_pubmed", "links": ["111", "222"]}]}]
            })
        )
        result = eutils.elink(client, "gene", "672")
    assert len(result) == 1
    assert result[0]["dbto"] == "pubmed"
    assert result[0]["linkname"] == "gene_pubmed"
    assert result[0]["ids"] == ["111", "222"]


def test_elink_empty(client):
    with respx.mock:
        respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi").mock(
            return_value=Response(200, json={"linksets": [{}]})
        )
        result = eutils.elink(client, "gene", "672")
    assert result == []


def test_elink_available(client):
    with respx.mock:
        respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi").mock(
            return_value=Response(200, json={
                "linksets": [{
                    "idchecklist": {
                        "idlinksets": [{
                            "linkinfos": [
                                {"linkname": "gene_pubmed", "dbto": "pubmed", "menutag": "PubMed"},
                                {"linkname": "gene_nuccore", "dbto": "nuccore", "menutag": "Nucleotide"},
                            ]
                        }]
                    }
                }]
            })
        )
        result = eutils.elink_available(client, "gene", "672")
    assert len(result) == 2
    assert result[0]["linkname"] == "gene_pubmed"
    assert result[1]["linkname"] == "gene_nuccore"
