import pytest
import respx
from httpx import Response

from ncbi_client import eutils
from ncbi_client.throttle import NCBIAPIError


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


FASTA_BODY = ">NM_007294.4 Homo sapiens BRCA1\nACGTACGTACGT\n"

EPOST_XML = (
    '<?xml version="1.0" encoding="UTF-8" ?>'
    "<ePostResult><QueryKey>1</QueryKey><WebEnv>MCID_abc123</WebEnv></ePostResult>"
)


def test_efetch_fasta_by_ids(client):
    with respx.mock:
        route = respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
            return_value=Response(200, text=FASTA_BODY)
        )
        result = eutils.efetch(client, "nucleotide", ["NM_007294"], rettype="fasta")

    assert result == FASTA_BODY
    sent_params = route.calls.last.request.url.params
    assert sent_params["id"] == "NM_007294"
    assert sent_params["rettype"] == "fasta"
    assert sent_params["retmode"] == "text"


def test_efetch_by_webenv_query_key(client):
    with respx.mock:
        route = respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi").mock(
            return_value=Response(200, text=FASTA_BODY)
        )
        result = eutils.efetch(client, "nucleotide", webenv="MCID_abc123", query_key=1, rettype="fasta")

    assert result == FASTA_BODY
    sent_params = route.calls.last.request.url.params
    assert "id" not in sent_params
    assert sent_params["WebEnv"] == "MCID_abc123"
    assert sent_params["query_key"] == "1"


def test_efetch_requires_ids_or_history(client):
    with pytest.raises(ValueError):
        eutils.efetch(client, "nucleotide", rettype="fasta")


def test_epost(client):
    with respx.mock:
        route = respx.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/epost.fcgi").mock(
            return_value=Response(200, text=EPOST_XML)
        )
        result = eutils.epost(client, "gene", ["672", "675"])

    assert result == {"webenv": "MCID_abc123", "query_key": 1}
    sent_request = route.calls.last.request
    assert sent_request.method == "POST"
    assert "id=672%2C675" in sent_request.content.decode() or "id=672,675" in sent_request.content.decode()


def test_epost_malformed_response(client):
    with respx.mock:
        respx.post("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/epost.fcgi").mock(
            return_value=Response(200, text="<ePostResult></ePostResult>")
        )
        with pytest.raises(NCBIAPIError):
            eutils.epost(client, "gene", ["672"])


def test_esearch_usehistory(client):
    with respx.mock:
        route = respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
            return_value=Response(200, json={
                "esearchresult": {
                    "idlist": ["672"], "count": "1", "retmax": "20", "retstart": "0",
                    "querykey": "1", "webenv": "MCID_abc123",
                }
            })
        )
        result = eutils.esearch(client, "gene", "BRCA1", usehistory=True)

    assert result["webenv"] == "MCID_abc123"
    assert result["query_key"] == 1
    assert route.calls.last.request.url.params["usehistory"] == "y"


def test_esearch_without_usehistory_omits_history_keys(client):
    with respx.mock:
        respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi").mock(
            return_value=Response(200, json={
                "esearchresult": {"idlist": ["672", "675"], "count": "2", "retmax": "20", "retstart": "0"}
            })
        )
        result = eutils.esearch(client, "gene", "BRCA1")

    assert "webenv" not in result
    assert "query_key" not in result


def test_esummary_by_webenv_query_key(client):
    with respx.mock:
        route = respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi").mock(
            return_value=Response(200, json={
                "result": {"uids": ["672"], "672": {"uid": "672", "name": "BRCA1"}}
            })
        )
        result = eutils.esummary(client, "gene", webenv="MCID_abc123", query_key=1)

    assert result[0]["name"] == "BRCA1"
    sent_params = route.calls.last.request.url.params
    assert "id" not in sent_params
    assert sent_params["WebEnv"] == "MCID_abc123"


def test_esummary_requires_ids_or_history(client):
    with pytest.raises(ValueError):
        eutils.esummary(client, "gene")


def test_elink_by_webenv_query_key(client):
    with respx.mock:
        route = respx.get("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/elink.fcgi").mock(
            return_value=Response(200, json={
                "linksets": [{"linksetdbs": [{"dbto": "pubmed", "linkname": "gene_pubmed", "links": ["111"]}]}]
            })
        )
        result = eutils.elink(client, "gene", webenv="MCID_abc123", query_key=1)

    assert result[0]["dbto"] == "pubmed"
    sent_params = route.calls.last.request.url.params
    assert "id" not in sent_params
    assert sent_params["WebEnv"] == "MCID_abc123"


def test_elink_requires_ids_or_history(client):
    with pytest.raises(ValueError):
        eutils.elink(client, "gene")
